//! Blocks that were fetched but not evaluated (D21).
//!
//! A block whose filter fails (its `eth_getLogs` or a receipt still fails after retries and URL
//! rotation) reaches the trigger stage with no matches, exactly like a block without matches. So
//! that such a block is never counted as evaluated, the block handler reports it here and the
//! caller that runs the handler collects the reports of its own run through a task-local scope:
//! the main cycle records them as missed blocks (retried by `recovery_config`), the recovery job
//! counts the attempt as failed. Outside a collecting scope a report is only logged by the caller.

use std::{
	collections::BTreeSet,
	future::Future,
	sync::{Arc, Mutex},
};

tokio::task_local! {
	static UNEVALUATED: Arc<Mutex<BTreeSet<u64>>>;
}

/// Reports that `block_number` was not evaluated by the filter.
///
/// Returns `false` when no caller collects the reports (outside `collect_unevaluated_blocks`).
pub fn report_unevaluated_block(block_number: u64) -> bool {
	UNEVALUATED
		.try_with(|blocks| {
			blocks
				.lock()
				.unwrap_or_else(|e| e.into_inner())
				.insert(block_number);
		})
		.is_ok()
}

/// Runs `fut` and returns its output with the blocks reported unevaluated while it ran.
///
/// Only reports made while `fut` itself is polled are collected: work that `fut` spawns onto
/// other tasks does not inherit the scope.
pub async fn collect_unevaluated_blocks<F: Future>(fut: F) -> (F::Output, BTreeSet<u64>) {
	let blocks = Arc::new(Mutex::new(BTreeSet::new()));
	let output = UNEVALUATED.scope(blocks.clone(), fut).await;
	let blocks = std::mem::take(&mut *blocks.lock().unwrap_or_else(|e| e.into_inner()));
	(output, blocks)
}

#[cfg(test)]
mod tests {
	use super::*;

	#[tokio::test]
	async fn test_reports_inside_the_scope_are_collected() {
		let (output, blocks) = collect_unevaluated_blocks(async {
			assert!(report_unevaluated_block(7));
			assert!(report_unevaluated_block(5));
			assert!(report_unevaluated_block(7));
			42
		})
		.await;
		assert_eq!(output, 42);
		assert_eq!(blocks.into_iter().collect::<Vec<_>>(), vec![5, 7]);
	}

	#[tokio::test]
	async fn test_a_report_outside_a_scope_is_not_collected() {
		assert!(!report_unevaluated_block(1));
		let (_, blocks) = collect_unevaluated_blocks(async {}).await;
		assert!(blocks.is_empty());
	}

	#[tokio::test]
	async fn test_scopes_do_not_share_reports() {
		let a = tokio::spawn(collect_unevaluated_blocks(async {
			report_unevaluated_block(1);
			tokio::task::yield_now().await;
		}));
		let b = tokio::spawn(collect_unevaluated_blocks(async {
			tokio::task::yield_now().await;
			report_unevaluated_block(2);
		}));
		let ((_, a), (_, b)) = (a.await.unwrap(), b.await.unwrap());
		assert_eq!(a.into_iter().collect::<Vec<_>>(), vec![1]);
		assert_eq!(b.into_iter().collect::<Vec<_>>(), vec![2]);
	}
}

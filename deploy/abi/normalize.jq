# Canonical layout of an ABI event/function entry, shared by deploy/check-abi.sh and deploy/abi/derive.sh.
# Two entries are the same only if every field that decides how the Monitor decodes a log or call is the
# same: name, anonymous flag, and for every input (and function output) its name, type, tuple components
# and, for event inputs, `indexed`, in order. internalType, stateMutability, errors, constructor and
# fallback entries carry no decoding information and are dropped.
def nio: {name: (.name // ""), type} + (if .components then {components: (.components | map(nio))} else {} end);
def canon: if (.type | startswith("tuple"))
             then "(" + ([.components[] | canon] | join(",")) + ")" + (.type | ltrimstr("tuple"))
             else .type end;
def sig: .name + "(" + ([(.inputs // [])[] | canon] | join(",")) + ")";
def norm_entry:
  if .type == "event" then
    {type, name, anonymous: (.anonymous // false), inputs: ((.inputs // []) | map(nio + {indexed: (.indexed // false)}))}
  elif .type == "function" then
    {type, name, inputs: ((.inputs // []) | map(nio)), outputs: ((.outputs // []) | map(nio))}
  else empty end;
def norm_abi: [.[] | norm_entry] | sort_by(.type + " " + sig);

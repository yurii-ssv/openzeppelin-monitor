# Type-level shape of the deploy templates (CP1A repair 4, F10). Included by check-templates.sh,
# check-abi.sh and check-m15-coverage.sh (`include "shape";` with -L deploy/).
#
# Every *_shape function maps ANY JSON value to an array of problem strings and never raises: a value
# of the wrong type is a problem line, not a jq runtime error. The callers still treat a failing jq as
# a failure of its own, so a problem here and a runtime error elsewhere are both red, never an empty
# "no violations" result.
# Absent / null optional fields are accepted here. Whether a field must be present and non-empty is
# the callers' business (S4-S6 of check-templates.sh keep their own messages for that).

def isa: if . == "array" then "an array" elif . == "object" then "an object" elif . == "string" then "a string"
         elif . == "number" then "a number" elif . == "boolean" then "a boolean" else "null" end;

# .[$k] must be present with type $t / may be absent or null but otherwise has type $t ($w prefixes the message)
def req($k; $t; $w): if (.[$k] | type) != $t then ["\($w)\($k) must be \($t | isa), got \(.[$k] | type)"] else [] end;
def opt($k; $t; $w): if (.[$k] != null) and ((.[$k] | type) != $t) then ["\($w)\($k) must be \($t | isa), got \(.[$k] | type)"] else [] end;
# the elements of the array .[$k] (when it is one) as {p: path, v: element}
def elems($k; $w): if (.[$k] | type) == "array" then [.[$k] | to_entries[] | {p: "\($w)\($k)[\(.key)]", v: .value}] else [] end;
def strings($k): [elems($k; "")[] | select(.v | type != "string") | "\(.p) must be a string, got \(.v | type)"];
def obj_or($w): if type != "object" then ["\($w) must be an object, got \(type)"] else [] end;

def address_shape($w):
  obj_or($w) + (if type == "object"
    then req("address"; "string"; "\($w).") + opt("contract_spec"; "array"; "\($w).")
         + [elems("contract_spec"; "")[] | select(.v | type != "object") | "\($w).\(.p) must be an object, got \(.v | type)"]
    else [] end);

def condition_shape($w; $sig):
  obj_or($w) + (if type == "object"
    then opt("expression"; "string"; "\($w).") + (if $sig then req("signature"; "string"; "\($w).") else opt("status"; "string"; "\($w).") end)
         + (if ($sig | not) and (.status | type) == "string" and (.status | IN("Any", "Success", "Failure") | not)
              then ["\($w).status \(.status) is not one of Any|Success|Failure"] else [] end)
    else [] end);

def monitor_shape:
  if type != "object" then ["monitor must be a JSON object, got \(type)"]
  else
    req("name"; "string"; "") + opt("paused"; "boolean"; "")
    + opt("networks"; "array"; "") + strings("networks")
    + opt("addresses"; "array"; "") + [elems("addresses"; "")[] | .p as $w | .v | address_shape($w)[]]
    + opt("match_conditions"; "object"; "")
    + (if (.match_conditions | type) == "object" then
         (.match_conditions
          | opt("events"; "array"; "match_conditions.") + [elems("events"; "match_conditions.")[] | .p as $w | .v | condition_shape($w; true)[]]
            + opt("functions"; "array"; "match_conditions.") + [elems("functions"; "match_conditions.")[] | .p as $w | .v | condition_shape($w; true)[]]
            + opt("transactions"; "array"; "match_conditions.") + [elems("transactions"; "match_conditions.")[] | .p as $w | .v | condition_shape($w; false)[]])
       else [] end)
    + opt("trigger_conditions"; "array"; "")
    + [elems("trigger_conditions"; "")[] | .p as $w | .v
        | obj_or($w)[], (if type == "object" then (req("script_path"; "string"; "\($w).") + opt("language"; "string"; "\($w).") + opt("timeout_ms"; "number"; "\($w).")) else [] end)[]]
    + opt("triggers"; "array"; "") + strings("triggers")
  end;

def envref_shape($w): obj_or($w) + (if type == "object" then opt("type"; "string"; "\($w).") + opt("value"; "string"; "\($w).") else [] end);

# one trigger definition: the value under its key in a triggers/*.json file
def trigger_shape($k):
  if type != "object" then ["\($k): trigger must be an object, got \(type)"]
  else
    req("name"; "string"; "\($k): ") + req("trigger_type"; "string"; "\($k): ") + req("config"; "object"; "\($k): ")
    + (if (.config | type) == "object" then
         (.config
          | opt("message"; "object"; "\($k): config.")
            + (if (.message | type) == "object" then (.message | opt("title"; "string"; "\($k): config.message.") + opt("body"; "string"; "\($k): config.message.")) else [] end)
            + (if .token != null then (.token | envref_shape("\($k): config.token")) else [] end)
            + (if .discord_url != null then (.discord_url | envref_shape("\($k): config.discord_url")) else [] end)
            + opt("chat_id"; "string"; "\($k): config.") + opt("language"; "string"; "\($k): config.")
            + opt("script_path"; "string"; "\($k): config.") + opt("timeout_ms"; "number"; "\($k): config."))
       else [] end)
  end;
# a triggers/*.json file: an object of trigger definitions keyed by trigger id
def trigger_file_shape:
  if type != "object" then ["trigger file must be a JSON object keyed by trigger id, got \(type)"]
  else [to_entries[] | .key as $k | .value | trigger_shape($k)[]] end;

def network_shape:
  if type != "object" then ["network must be a JSON object, got \(type)"]
  else
    req("slug"; "string"; "") + opt("name"; "string"; "")
    + opt("rpc_urls"; "array"; "")
    + [elems("rpc_urls"; "")[] | .p as $w | .v | obj_or($w)[],
        (if type == "object" then (opt("weight"; "number"; "\($w).") + (if .url != null then (.url | envref_shape("\($w).url")) else [] end)) else [] end)[]]
    + opt("max_past_blocks"; "number"; "") + opt("recovery_config"; "object"; "")
  end;

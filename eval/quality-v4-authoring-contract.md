# Independent controlled evaluation authoring contract

This describes the execution environment and file interfaces, not the implementation or its past performance. Only begin independent authoring after the root supplies a frozen product manifest and its timestamp. Do not read product code, previous datasets, old outputs, or scores. Do not contact real merchants. Author 6 genuinely new source groups and entities, with original prose and realistic everyday tasks; not paraphrases of supplied cases. No gold/check descriptions inside actor input or source prose.

Use exactly one case in each family: reading, offers, routes, edits, recovery, boundaries. Four use driver=read; edits uses driver=edit, recovery uses driver=save_restart. Four case_class=delivery and two=bounded_answer. A bounded answer must explain a well-defined uncertainty/conflict from evidence; a delivery must produce the requested concrete calculation/comparison/draft. Do not count a generic refusal as a successful positive task. Do not design all tasks around one wording or one kind of source.

Sources are explicitly synthetic supplied observations, not scraped live websites. A read case supplies one source packet whose text may contain several clearly separated documents or alternatives. Ask for analysis of supplied facts; no login/real inventory, write, purchase, booking, or messaging. Preserve genuine missing facts and distinguish item price, face value, known subtotal, conditional calculation, and complete requested cost. Wording and numbers are chosen by the author, not the executor. Use an explicit as_of timestamp (Asia/Shanghai) and explain any time-sensitive premise in source/gold. A runtime imported draft is a controlled initial condition, not an actor achievement.

Files, in the output dataset directory named by root:
- `sources.json`: list with source_id, group_id, title, text, observed_at (ISO timestamp with zone), origin='synthetic_controlled', scope. IDs ASCII alphanumeric/hyphen/underscore, unique. For edit/recovery also `structured_initial_state: {<case_id>: <same initial_state object as task>}`; the source's prose must explicitly state that initial state and unknown business facts.
- `tasks.json`: list with case_id, family, case_class, group_ids=[source group, entity group], source_ids=[one source_id], as_of, scenario_origin='synthetic_controlled', scope, agent_input, environment. IDs match sources/gold.
- `gold.json`: list with case_id, status='draft_pending_independent_ai_review', human_reviewers=[], must_pass=[{id:'C1',description:'...'},...], forbidden_claims=[string,...], gold_facts=[{id:'F1',proposition,source_id,quote},...], accepted_variations=[string,...]. All source quotes must occur exactly in their source; calculations require a verifiable derivation. Keep gold independent of implementation field layout, accept mathematically/semantically equivalent representations. For example effective total budget is min(explicit total cap, party_size * per-person cap) over existing caps; do not require a redundant total-cap field if a per-person cap is equivalent. If no cap exists it is unknown/unlimited as user declares, not zero.
- `protocol.json`: version='plango.controlled-bounded.v1', name, evaluation_kind='independent_controlled', planned_case_ids (task order), primary_source_groups=6, case_authoring (truthful independence), execution_scope (4 supplied observations + 2 actual Electron state workflows), limits={calls:80,case_calls:12,reported_tokens_stop:120000}, budget_ledger='output/tightening-F9A9ea/model-budget.jsonl'. This is shared with the regression batch, not a fresh allowance.
- `authoring.json`: author identity, actual creation timestamp, files read, independence statement, brief coverage rationale. Do not self-approve gold.

Read case:
`agent_input={user_turns:[{message:<natural user request>}]}` and `environment={driver:'read',expected_stop:'terminal'}`. The actual model receives source observation through the application's normal browser observation path. It may extract/interpret, but supplied facts are not guaranteed to be understood. Gold can accept a table, cards, or coherent text, provided the required information and source association are present.

Edit case:
`agent_input={user_turns:[{message:<first sparse edit>},{message:<second sparse edit>}]} `; exactly two natural-language turns, final user goal is a visible reviewable draft, not automatic save. `environment={driver:'edit',expected_stop:'draft_review',initial_state: ...}`.

Save/restart case:
`agent_input={}`, `environment={driver:'save_restart',expected_stop:'saved_restored',initial_state: ...}`. Driver saves once through actual UI, closes/reopens owned API/DB and Electron, reads original run/profile. No new user message, resave or new run is permitted during recovery. Backend lifecycle is recreated; do not require whole-machine reboot or claim that it is tested.

Initial-state shape for those two cases (choose your own meaningful values):
```
{
 "run_id":"fixture:<unique-case-alias>", "plan_version":<positive integer>,
 "spec":{
   "party_size":<1..12>, "visit_date":"YYYY-MM-DD", "time_window_start":"HH:MM",
   "duration_minutes":<30..1440>, "budget":<nonnegative number or null>,
   "per_person_budget":<nonnegative number or null>,
   "search_radius_km":<0.1..50>, "route_distance_km":<0.1..1000>, "travel_mode":"walking",
   "selected_poi":{"id":"fixture:<unique-place>","name":<synthetic name>,"address":<explicitly fictional address>},
   "selected_offer":{"source_id":<same source_id>,"offer_id":"fixture:<unique-offer>",
                      "price":<nonnegative number>,"face_value":<number or null>,"rules_status":"unknown"}
 }
}
```
The runtime maps aliases to actual IDs and initializes a not-saved single-stop draft. The declared shared world has origin (29.56,106.57), selected place (29.561,106.571), one walking leg 0.2km/3min/zero fare, unknown supply/weather/full meal price. Root will generate `runtime-fixtures.json` from the existing environment adapter; do not author hidden fixtures or overwrite them. Gold should test preservation and user-requested changes, not discovery of the seeded place. No evidence claims of actual reservation/payment are permitted.

After authoring, another independent context reviews all source/gold cases before actor execution. Any actual oracle error is documented; low product scores never justify changing gold. All attempts and costs remain recorded. The author must not inspect implementation/actor results or rewrite sources after publication of the dataset.

## Two requirements added after the v6 batch

**Disclose auto-injected context.** This workspace injects `AGENTS.md` into every AI
session before the first action, and both the v6 author and the v6 reviewer found that
it had entered their context without being read deliberately. Any author or reviewer
must record, in `authoring.json` or the review's `scope`, every instruction file the
environment supplied and assess what it exposed. Independence here means authored after
the freeze with no access to implementation, past cases, outputs or scores; it does not
mean the context is free of all information about the system under test. State that
limit with the result rather than claiming more. Every gold requirement must be
derivable from this contract alone; if a requirement can only be explained by something
learned from injected context, drop it.

**Keep `must_pass` to what should block success.** Each entry is conjunctive: one
failure sinks the case. The v6 reviewer identified three entries that were desirable
rather than load-bearing, including one requiring an assertion about a question the user
never asked, and one asking a state-only case with no user message to volunteer a list
of unknowns. Put a requirement in `must_pass` only when a correct delivery is impossible
without it, and only when the user's own request implies it. Anything else belongs in
`accepted_variations`, or is left out. Note that `must_pass` counts differ between
batches, so success rates are not comparable across datasets.

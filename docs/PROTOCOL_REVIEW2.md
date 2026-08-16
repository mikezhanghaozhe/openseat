# Protocol review 2

## Gap 1 — The automatic hand lifecycle has no canonical event sequence

**What's underspecified.** The callable REST path is clear only at its coarse boundaries:
`POST /v1/rooms` with `game`, `seats`, `config`, and optional test-only `seed`; one
`POST /v1/rooms/{id}/seats` per seat with `invite_token`, `seat`, `kind`, and `display_name`;
`POST /v1/rooms/{id}/start` with `host_token`; repeated `GET /v1/rooms/{id}/view`; and
`POST /v1/rooms/{id}/actions` with `seat_token`, `request_id`, `action`, and optional
`table_talk`. After the last action, clients can call `GET /v1/rooms/{id}/result`.

What happens between those calls is not ordered. The protocol names `room_created`,
`seat_joined`, `hand_started`, `blinds_posted`, `hole_cards_dealt`, `action_required`,
`action_taken`, `board_dealt`, `showdown`, `pot_awarded`, and `hand_complete`, but never specifies
their canonical ordering. It also does not say whether `table_talk` precedes or follows the
corresponding `action_taken`; when street bets are collected; when `pots` becomes observable; or
whether the last action on a street is followed by `board_dealt` and `action_required` within the
same atomic application.

**Why it matters.** A client replaying only the public log cannot reliably reconstruct the same
phase transitions. More importantly, deterministic replay promises a byte-identical event log,
which is impossible to test without a single required event order.

**Options.**

1. Specify a complete event state machine, including exact event order for room setup, every
   street transition, uncontested award, contested showdown, and completion.
2. Make each client action emit only `action_taken`; expose all automatic transitions solely in
   the next view. This simplifies logs but makes the log cease to be the transcript.
3. Define a compound transition event containing the action and every automatic consequence.

**Recommendation.** Option 1. Require the ordinary setup order
`room_created`, `seat_joined` entries, `hand_started`, `blinds_posted`, `hole_cards_dealt`,
`action_required`; then define the exact suffix for each kind of action and terminal path.

## Gap 2 — A successful action response does not define which sequence number it returns

**What's underspecified.** `POST /v1/rooms/{id}/actions` returns `{ "seq": 48,
"accepted": true }`, but one action can cause several events: at minimum `action_taken`, and
possibly `table_talk`, `board_dealt`, `action_required`, `showdown`, `pot_awarded`, and
`hand_complete`. The protocol does not say whether `seq` identifies `action_taken`, the final
event caused synchronously by the request, or the room's latest event at response serialization.
The same ambiguity affects the `seq` returned by `/start`.

**Why it matters.** A polling client must know which value to pass as `events?since=` and whether
it has observed all consequences of its request. An unrelated concurrent read or automatic
transition can otherwise create missed or duplicate processing.

**Options.**

1. Return `first_seq` and `last_seq` for the atomic transition.
2. Define the existing `seq` as the last event committed by that request.
3. Return the complete emitted `events` array.

**Recommendation.** Option 1. It preserves compact responses while identifying the exact event
range caused by the request.

## Gap 3 — Initial button, first actor, and seed derivation are not reproducible from the protocol

**What's underspecified.** `POST /start` returns `button`, `to_act`, and `seq` indirectly or in the
view, but nothing specifies how the first `button` is selected. The example's `to_act: 0` cannot
be treated as a rule. Section 10 says `hand_seed = derive(master_seed, hand_no)` without defining
`derive`, integer encoding, endianness, shuffle algorithm, or deck ordering.

**Why it matters.** A second implementation cannot reproduce the deal or event log from the
disclosed `hand_seed`. Even the same implementation may change results after a dependency or
shuffle refactor, defeating the stated replay purpose.

**Options.**

1. Pin a named derivation function, deck order, shuffle algorithm, and initial-button rule in the
   protocol.
2. Persist and disclose the post-shuffle deck after `hand_complete` instead of promising seed-only
   replay.
3. Narrow reproducibility to one exact server build and record a build identifier in
   `hand_complete`.

**Recommendation.** Option 1, with an explicit algorithm version included in the hand transcript.

## Gap 4 — Completion and public result endpoint behavior are incomplete

**What's underspecified.** `GET /v1/rooms/{id}/result` has no authentication rule, no status or
error before `hand_complete`, and no definition for its `winners`, `pots`, or `final_stacks`
shapes. `410 room_closed` says a finished room is closed, but the protocol does not say whether
that applies to `/view`, `/events`, `/result`, `/start`, or only mutating calls. All responses are
said to include `protocol_version`, yet the endpoint examples omit it.

**Why it matters.** The final step from `pot_awarded` to reading a result requires invention.
Implementations can make the result unavailable precisely because the room is finished, or expose
different data under the same field names.

**Options.**

1. Give every endpoint a complete response schema and endpoint-specific pre-start, active, and
   completed-room behavior.
2. Remove `/result` and require consumers to derive results from public events.
3. Keep `/result` but define it as a mechanically selected projection of `pot_awarded`,
   `showdown`, and `hand_complete` events.

**Recommendation.** Option 3, plus explicitly keep `/events` and `/result` readable after room
completion and return a named conflict such as `409 hand_in_progress` before completion.

## Gap 5 — The disconnect/reconnect scenario cannot be walked consistently

**What's underspecified.** In M1 there is no connection state and the turn clock is inert, so a
seat that stops polling cannot be carried forward two streets: the hand waits forever if that seat
is `to_act`. In M2, reconnect requires `POST /v1/rooms/{id}/ws-ticket`, opening
`/v1/rooms/{id}/ws?ticket=...`, and sending `{ "t": "resume", "since": N }`, but the race among
ticket issuance, timer expiry, replay, and the final `state` frame is not defined. `deadline_ms`
is also ambiguous between an absolute Unix timestamp, duration, and remaining time. The `hello`
frame says it contains replay “from 0” even though reconnect separately supplies `since`.

**Why it matters.** The requested two-street reconnect is impossible in M1 and nondeterministic in
M2. A reconnecting client can act on an expired state or process the same full history twice.

**Options.**

1. Declare this scenario M2-only and specify one atomic resume snapshot: replay `since + 1` through
   a captured `latest_seq`, then send the view at exactly that sequence; express deadlines as
   absolute server epoch milliseconds.
2. Enforce the turn clock in M1 as well, making REST polling sufficient to observe advancement.
3. Pause a disconnected seat's clock, which changes poker semantics and enables stalling.

**Recommendation.** Option 1. Also state that timeout wins any race once its deadline has passed
at the server, even if a reconnect or action is concurrently received.

## Gap 6 — A busted seat has no next-hand transition

**What's underspecified.** After `pot_awarded` and `hand_complete`, a zero stack can be represented
as `status: "busted"`, but §0.1 explicitly provides no next-hand endpoint or trigger and defers
button/blind rotation around busted seats. Therefore “a seat busts to zero and the next hand
starts” has no protocol-defined next call or field transition.

**Why it matters.** This is an intentional M1 scope boundary, but it means the scenario cannot be
implemented or tested from protocol 0.1. Retaining `room_complete` and multi-hand vocabulary makes
the boundary less obvious to clients.

**Options.**

1. Keep it out of 0.1 and remove or mark all multi-hand fields/events as reserved and unusable.
2. Define a minimal host-only `POST /next-hand` plus busted-seat skipping.
3. Auto-start the next hand after a fixed transition and end when fewer than `min_players` have
   chips.

**Recommendation.** Option 1 for M1; require a protocol version change before option 3 is added.

## Gap 7 — “Receipt order” is not an externally defined concurrency rule

**What's underspecified.** Invariant 6 serializes actions “in receipt order,” but does not define
the receipt point when two HTTP requests arrive simultaneously: socket accept, complete-body read,
lock acquisition, or queue insertion. It also does not specify the response expected when the
current actor and the next actor submit concurrently and the latter is processed before or after
the former.

**Why it matters.** Different workers and event loops can choose different winners. The same pair
of requests can yield `403 not_your_turn` or two accepted actions, changing both state and log.

**Options.**

1. Define receipt as insertion into a single per-room ordered queue and return a server-assigned
   monotonic receipt number.
2. Permit either ordering and require clients to rely only on the response and `seq`.
3. Add an optimistic precondition such as `expected_seq` to every action.

**Recommendation.** Combine options 1 and 3. Queue order makes server behavior coherent;
`expected_seq` prevents a pre-submitted next-seat action from becoming valid merely because an
earlier queued request advances the turn.

## Gap 8 — Idempotency does not define the compared request or rejected-request behavior

**What's underspecified.** A repeated `request_id` with a different `action` must return
`409 request_id_conflict`, but the protocol does not say whether `seat_token` or `table_talk` is
part of identity; whether JSON normalization matters; whether IDs are scoped per seat or room;
whether malformed, unauthorized, wrong-turn, or illegal requests reserve an ID; or how long the
original result is retained. A same-ID/different-body race is not explicitly part of the room's
atomic section.

**Why it matters.** The requested duplicate-ID case can produce replay, conflict, or a newly
accepted action depending on which fields and outcomes are cached. Including `table_talk` can also
cause a retry to conflict after the poker action already succeeded.

**Options.**

1. Scope IDs to `(room_id, seat)`; compare a canonical action only; reserve the ID only when an
   action commits; retain through room expiry; atomically check/store with application.
2. Hash the entire semantic body, including `table_talk`, and cache every response.
3. Make IDs globally unique and retain them indefinitely.

**Recommendation.** Option 1. Treat table talk as a separately identified operation if it needs
independent retry guarantees.

## Gap 9 — Public table talk defeats the absolute hidden-information event contract

**What's underspecified.** `POST /actions.table_talk` accepts arbitrary player-controlled text,
and `table_talk` rebroadcasts `{ seat, name, text }` to every seat and spectator. Seat 2 already
knows its own cards and can submit “I have As Kd”; seat 1 then learns them before showdown. This
contradicts “An event never contains hidden information.” The observation's `chat` and LLM
`text` renderer then repeat the disclosure.

**Why it matters.** This is a direct redaction bypass that never touches raw `GameState` and
cannot be solved by `GameAdapter.view`. A model seat may accidentally disclose cards through
generated speech, and the public event log preserves the disclosure.

**Options.**

1. Remove table talk until after the hand.
2. Permit voluntary disclosure and narrow the invariant to server-derived hidden information.
3. Attempt card-token filtering or semantic moderation, accepting both bypasses and false
   positives.

**Recommendation.** Option 2 if table talk is product-essential; otherwise option 1. Do not rely
on filtering as a secrecy boundary.

## Gap 10 — Public summaries, errors, and timing bypass the stated redaction chokepoint

**What's underspecified.** `GET /rooms/{id}` has no fixed response schema and only says it never
contains stacks-in-hand detail “beyond what's already public.” Error bodies allow arbitrary
human-readable `reason` and `...context`; no rule says they must be constructed only from the
request and the caller's redacted observation. Public event/error response timing is unconstrained,
so hidden-card-dependent showdown evaluation, serialization, or logging may be distinguishable.
Spectators can repeatedly read the public summary and events and amplify each of these channels.

**Why it matters.** Seat 1 need not receive a `hole` field if a reason, context value, summary
field, or reliably measurable delay encodes seat 2's cards or hand strength. None of these paths is
forced through `GameAdapter.view`.

**Options.**

1. Define closed schemas for the summary and every error; require all dynamic context to be a
   projection of public events or the caller's view; audit timing-sensitive operations.
2. Route even public summaries and errors through explicit adapter redaction methods.
3. State that timing side channels are out of scope while still closing schema-based channels.

**Recommendation.** Option 1, with option 3 only as an explicit, risk-accepted timing policy.

## Gap 11 — “Server-side transcript” conflicts with the public event log and `ts` breaks byte identity

**What's underspecified.** Section 5 says the public JSONL event log “is the transcript.” Section
10 simultaneously permits the in-progress `hand_seed` in a “server-side transcript only,” while
forbidding it in every event or response. No second private transcript is defined. Separately,
every event includes `ts`, but same seed plus same actions is promised to produce a byte-identical
event log; wall-clock timestamps necessarily differ, and no timestamp source or canonical replay
rule is specified.

**Why it matters.** Treating the private seed record as the public transcript leaks the entire
deck to seats and spectators. Treating `ts` literally makes the determinism invariant false.

**Options.**

1. Define separate private hand metadata and public event-log stores; forbid secrets in the
   latter; exclude `ts` from deterministic comparison.
2. Replace `ts` with deterministic logical time derived from `seq`.
3. Remove `ts` and retain only `seq`.

**Recommendation.** Option 1. Name the private store something other than transcript and define
exactly when its seed is copied into the public `hand_complete` event.

## Gap 12 — Showdown order, incremental visibility, and muck semantics are not fixed

**What's underspecified.** When a non-all-in seat reaches showdown, the protocol says `to_act`
“names the seat with the decision,” but does not define the first seat or subsequent order. It
says PokerKit can be driven “in whatever order you like,” which permits different logs and can
change `can_win_now` as hands become visible. It also does not say whether each `show` becomes
immediately visible in views/events or whether one aggregate `showdown` event is emitted after all
choices. Finally, a seat is offered `muck` without saying whether mucking a winning hand forfeits
its pot eligibility.

**Why it matters.** Show order changes strategic information, timeout behavior, what seat 1 can
learn about seat 2 before seat 1 chooses, and potentially the winner. An aggregate-only event also
leaves reconnecting clients unable to reconstruct a partially completed showdown.

**Options.**

1. Adopt PokerKit's `showdown_indices` order, emit one public reveal/muck event per choice, and
   define muck as forfeiting eligibility.
2. Compute all choices privately and emit only one final aggregate event.
3. Force every live hand to show, eliminating showdown turns.

**Recommendation.** Option 1. It aligns the wire order with the rules engine and makes partial
showdown replayable.

## Gap 13 — Unequal-stack all-ins lack award and odd-chip rules

**What's underspecified.** In a three-way flop all-in, clients can use
`legal_actions[].raise.min_to/max_to` and `call.amount`, then observe `board_dealt`, `pots`,
`showdown`, and `pot_awarded`. The protocol does not specify when each side-pot entry first appears,
how indices remain stable as bets are collected, whether an uncalled excess is returned and
reported, or how an indivisible chip is assigned when a pot splits. `pot_awarded.winners` alone
cannot represent unequal per-winner awards.

**Why it matters.** Three different stack sizes commonly create multiple pots, and split pots can
leave remainders. `amount` plus `winners` is insufficient to derive final stacks unless the
remainder rule is known.

**Options.**

1. Add `awards: [{ seat, amount }]` to every pot and specify PokerKit's exact odd-chip/order rule.
2. Define an independent protocol rule, such as awarding odd chips clockwise from the button.
3. Require chip denominations that always divide evenly, which is not generally enforceable.

**Recommendation.** Option 1. Treat award amounts, rather than winner count, as authoritative.

## Gap 14 — The raise-bound rule rejects a legal PokerKit all-in raise

**What's underspecified.** Section 3 says omit `raise` when `max_to <= min_to`, then says an
all-in-for-less is a raise whose `min_to == max_to`. PokerKit 0.7.4 demonstrably permits this
case: after an opener raises to 10, a player with total street capacity 15 receives both minimum
and maximum 15, and `complete_bet_or_raise_to(15)` succeeds. Under the protocol's omission rule,
that action is unreachable. PokerKit's no-limit maximum is
`state.stacks[actor] + state.bets[actor]`, not merely the actor's remaining `stack`.

**Why it matters.** A legal short-stack all-in is removed from `legal_actions`, violating both
poker rules and the assertion that all-in-for-less needs no special action.

**Options.**

1. Include `raise` whenever both bounds are non-`None` and `max_to >= min_to`; allow equality.
2. Add a separate `all_in` action for the equality case.
3. Represent it as `call`, which is incorrect when it exceeds the current wager.

**Recommendation.** Option 1, and define `max_raise_to` as current street commitment plus
remaining stack.

## Gap 15 — Several §10 PokerKit derivations have the wrong type or incomplete domain

**What's underspecified.** Against pokerkit 0.7.4 source:

- `state.pots` is an `Iterator[Pot]`, not a list. It must be materialized once if multiple fields
  are derived from the same snapshot.
- `state.checking_or_calling_amount` is `int | None`; §4 presents `to_call` as an unconditional
  integer even when there is no actor, including showdown and terminal phases.
- `not state.statuses[i]` means the player is inactive, but inactivity can also result from
  showdown hand killing; it is not a durable room-level `folded`/`busted`/`sitting_out`
  classification.
- `state.starting_stacks[i] - state.stacks[i]` includes live `bets` correctly mid-hand, but has no
  defined snapshot instant before automated chip pushing and cannot derive a completed view.
- `state.can_win_now` requires `player_index` and means the hand can beat already exposed hands in
  at least one eligible pot; it does not mean an omniscient comparison with every hidden hand.

**Why it matters.** Exhausting a generator twice can produce inconsistent `pots` and `pot_total`;
serializing `None` where the schema implies an integer breaks clients; and overloaded status or
win predicates can mislabel showdown seats and encode the wrong timeout policy.

**Options.**

1. Define phase-specific nullable fields and maintain explicit protocol status/commitment
   snapshots alongside PokerKit state.
2. Omit actor-only fields whenever `to_act` is `null` and omit `committed_hand` after completion.
3. Expose PokerKit's raw state distinctions directly, coupling the protocol to the dependency.

**Recommendation.** Combine options 1 and 2. Materialize `tuple(state.pots)` once per view/event
construction and document the exact `can_win_now(seat)` semantics.

## Gap 16 — §10 overstates PokerKit's all-in showdown automation

**What's underspecified.** Section 10 says that when all contested seats are all-in, showdown
resolves automatically “regardless” of `HOLE_CARDS_SHOWING_OR_MUCKING`. In pokerkit 0.7.4, with
the protocol's proposed automation flags and that flag absent, a three-player preflop all-in
leaves `showdown_indices` populated and `can_show_or_muck_hole_cards()` true; the caller must drive
those operations. PokerKit defaults each all-in show decision to showing, but does not itself
perform the operations without the automation flag. The protocol also says to force
`runout_count` to 1, but does not specify the state mode or calls that suppress cash-game runout
selection.

**Why it matters.** An adapter implemented literally can stall before dealing the remaining board,
or expose a discretionary `show`/`muck` phase that the wire contract says must not exist. Different
automation configurations also produce different operation and event ordering.

**Options.**

1. Enable `HOLE_CARDS_SHOWING_OR_MUCKING` and translate its operations into the required aggregate
   public reveal behavior for all-in showdowns.
2. Keep it disabled and explicitly loop over `showdown_indices`, calling
   `show_or_muck_hole_cards(True, seat)` for every live all-in seat.
3. Change the wire protocol to expose those show decisions even though they are mandatory.

**Recommendation.** Option 2 for maximum control over emitted events, with an explicit
`runout_count = 1`/tournament-mode configuration procedure verified by an adapter test.

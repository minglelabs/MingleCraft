# Jev policy and parallel question contract

This document describes the live MingleCraft contract and retains older strategy and replay notes below as archival references. Noul and Choice are two inference roles of the same Jev/System One model; they are not separately trained reinforcement-learning networks or measured win rates.

## Current live runtime

The live Jev path currently omits the bundled strategy and cumulative match history from remote requests following context-limit failures. The strategy text below remains an archived reference; it is not the active wire prompt. `--strategy-file` does not enable strategy transmission in this mode. Full observations, legal actions, receipts, and match history remain available locally; remote input retains current visible state and dated enemy memory. Compact representations must preserve the available candidates and their exact local execution mapping.

One provider request contains every tree question and every independent 4x4 coordinate-digit question, plus the optional Noul forecast. A Choice answer cannot read the Noul answer from the same call. The request includes a map-specific common policy once. Size is checked in UTF-8 bytes and estimated tokens; the estimate is not Jev's official tokenizer. Validate gameplay with fresh live decision and receipt traces.

## Compact live wire format

Direct Jev and OpenRouter Jev requests use `state.schema = "jev/compact-v1"`. Local observations, actions, and execution envelopes keep the normal typed objects. Only provider-bound JSON changes:

- Observed entities become `columns` / `rows` tables. Every supported legal action remains a local tree leaf; its description is in that leaf's Choice criterion. The entire raw action table is not duplicated in the provider request. Baseline-only priority scores stay private.
- Positions use `[x, y]`; command and construction-site rows have an explicit legend.
- Choice questions use command kind → actual unit or unit group → executable command or target. Every non-singleton node is asked in the same request, with branch assumptions in its instructions. Code validates only the selected path. Every question remains under 255 choices.
- Spatial questions encode successive base-4 x/y digits of one absolute destination. The model answers them independently; digit consistency is an assumption, not a guarantee. There is no later coordinate call.
- The optional Noul value question shares the request and cannot supply a fresh estimate to the Choices. Request bytes and estimated tokens are logged; actual token usage comes from the provider response.

Current visible enemies and dated last sightings remain distinct. This encoding does not reconstruct information missing from BWAPI, restore cumulative history to the prompt, invent targets, or enable model-generated actions.

### Archived offline replay measurement (2026-09-21)

The captured run `bwapi_29908_1789891637947956` was re-encoded without making API calls. Sizes include model, state, questions, and the compact legend. The old side uses the captured request (already excluding strategy and history); the new side preserves its observations and candidates. This is not a live performance or exact token benchmark.

| Frame | Candidates | Stage | Original bytes | Compact bytes | Reduction |
| --- | ---: | --- | ---: | ---: | ---: |
| 0 | 58 | Value | 15,766 | 9,503 | 39.7% |
| 0 | 58 | Policy | 24,209 | 15,284 | 36.9% |
| 2199 | 212 | Value | 60,344 | 32,617 | 45.9% |
| 2199 | 212 | Policy | 85,471 | 45,655 | 46.6% |

The captured frame-2199 value call used 32,306 Jev input tokens; the following policy call failed with HTTP 400. New live token counts, error rates, decision quality, and latency remain unmeasured. Large later-game states can still exceed provider limits; compression is not an unbounded-context guarantee.

## Archived reference prompts

### Value stage: Noul win probability

```text
Will our player ultimately win this StarCraft: Brood War match,
conditional on the current observed state and continuing to select legal actions
under `strategy_policy`? Return the Noul probability of yes, not a positional
Score, action preference, or separate confidence. A draw counts as not winning.
Read `observation`, `candidate_actions`, and the complete chronological
`match_history`. History observations are top-level deltas: unchanged fields
carry forward; a replaced list replaces its previous contents. Each event has
frame and game_seconds. Visible sightings are facts only at their timestamp;
unseen enemy information remains unknown. An issued decision is only intent;
a command_receipt reports acceptance, not completion. Infer effects only from
subsequent observed changes. Do not treat missing observations as negative evidence.
Use the supplied strategy's strategic reference and factual uncertainty rules;
its Choice-only response instructions apply exclusively to the policy stage.
Consider economy, production, technology, army composition, terrain, current
threats, scouting age, and feasible continuation. Do not invent missing facts.
`latest_value` and history value_estimate events are previous model estimates,
not independent evidence or ground truth. Reassess from observations rather
than anchoring on those estimates; the current estimate does not exist yet.
This is an uncalibrated forecast until validated against actual match outcomes.
```

This question returns the Noul probability that the player eventually wins. It is a forecast for the current state and legal continuation; a draw counts as not winning. It is not a Score, confidence, action preference, or empirical calibration claim.

### Policy stage: Choice action selection

```text
Select the legal option that best advances our eventual
chance of winning under `strategy_policy`, using `observation`, every supplied
`candidate_actions` entry, and complete timestamped `match_history`.
Read history observation deltas chronologically; unchanged fields carry forward
and replaced lists replace their previous contents. Distinguish sightings from
hypotheses, issued commands from receipts, and acceptance from observed effects.
The current `latest_value` is this observation's value-stage forecast. Historical
forecasts are estimates, not fresh scouting, independent evidence, or measured
win rates. A low forecast does not justify an illegal action or a blind attack;
a high forecast does not justify ignoring defense or idle production.
Re-evaluate immediate threats, uncertainty, economy, supply, and the supplied
conditional strategy. Compare only this question's criteria. For child questions,
use the supplied state and the branch described in that question's instructions.
All questions in this same call are evaluated against the same state; the
application follows the selected node references to one executable leaf action.
Choose only an existing option ID. Code maps it to the exact BWAPI command;
never generate a command or invent a target. Keep valid orders with wait when
no new action is justified. Return only the requested Choice answer, with a
probability for every option and confidence based on evidence.
```

This stage returns Choice answers for the finite candidate hierarchy. The probabilities compare the options in that question; they are not a win probability. Confidence describes support for the current choice from the supplied evidence.

## Shared supplied strategy

The following is the archived shared strategy, currently omitted from both remote stages. It is the attached strategy verbatim and is sourced by `SHARED_POLICY` from `strategy/prompt.py`.

```text
You are a strategic decision maker for StarCraft: Brood War 1v1 under partial observability. At every observation, choose one option from the finite set of legal action candidates. Combine scouting facts, resources, production, army, terrain, technology, upgrades, and the age of the enemy's last sighting. The goal is not to recite a build order mechanically. The goal is to choose the action with the highest chance of survival and the best continuation from the current state.

This document is a strategic reference and decision policy. It is not a fixed script or a guarantee of victory, and it does not define one universally best modern build. Map, spawn positions, scouting, actual income, worker losses, cancelled construction, and unit composition change both supply counts and timings. Treat the numbers below as reference checkpoints. The observed state and legal action candidates take precedence. If a build is late, do not force every later instruction. Recompute using a legal action that preserves the same strategic purpose.

## 1. Boundary between the judge and the executor

1. Jev may choose only from the finite options in `criteria`. Do not invent a unit, building, technology, command, location, cost, cooldown, or action that is not present in the candidates. If the current candidates are centered on Terran versus Terran with SCVs, Marines, Supply Depots, and Barracks, keep the actual decision within that scope.
2. Jev judges strategic intent and risk. Code validates resource cost, supply, production availability, construction locations, unit capabilities, and command expiry. Do not choose an impossible candidate merely because its strategic idea sounds good.
3. Separate observed facts from inferences in `state`.
   - Facts: currently visible units, their last seen positions and `age_frames`, confirmed buildings, resources, supply, completed technology, living friendly units, and actual action candidates.
   - Inferences: the unseen enemy's intention, hidden technology, an unobserved expansion, and the next attack timing.
   - Do not present an inference as a fact. Keep multiple explanations alive.
4. Do not treat an unseen enemy as certainly strong or certainly absent. As the last sighting gets older, widen the possibility distribution and increase the value of safe scouting, defense, and detection candidates.
5. Each Choice question evaluates only its own stage. Evaluate a child stage assuming the parent strategy has already been selected. Do not copy the same answer into every question. Compare the risk and objective addressed by each candidate.
6. Set confidence and probabilities in proportion to the evidence. If scouting is incomplete or candidate probabilities are close, use lower confidence and prefer information, defense, or preserving valid existing orders over a risky attack.
7. `wait` does not mean blindly doing nothing. It means preserving a valid production, movement, or defense order until the next observation. Choose wait when no new command is justified or when no candidate matches the current intent.

## 2. Reading state and handling uncertainty

Use the following fields when present. Do not fill missing fields with guesses.

- `matchup`, `self_race`, `enemy_race`: race and matchup. The current MingleCraft v0.1 Observation is limited to Terran versus Terran, so other matchups can only be executed after the schema, state, and action candidates are extended.
- `frame`, `game_seconds`: Brood War uses approximately 24 frames per second, but frames are reference points. Network delay, game speed, and map travel distance change actual arrival times.
- `resources`, `supply_used`, `supply_total`: spendable resources and available supply. Account for reserved construction and production costs and buildings already in progress.
- `own_counts`, `completed_counts`, `production`, `tech`, `upgrades`, `bases`, `workers`: distinguish completed, under-construction, training, and idle entities.
- `enemy_visible`: only units currently visible are facts.
- `enemy_last_seen`: use the last seen position together with `age_frames`. An old position is not the enemy's current position.
- `candidate_actions`: actions that code has approved as executable. A strategic objective with no candidate must wait for a later observation or be represented by a legal fallback.
- `locations`: `start` locations are possible-location hypotheses. Do not confirm an enemy base from that label. If exploration status is missing, leave it unknown.
- `fog_of_war`: unseen areas provide no evidence that units, technology, expansions, or traps are absent.

For every decision, internally answer these four questions:

1. What do I know for certain right now?
2. What are the three most dangerous explanations for the enemy state? For example: fast expansion, one-base technology, or immediate all-in.
3. What scouting result or defensive checkpoint would distinguish those explanations?
4. Until that checkpoint is known, which legal action preserves the most options while surviving at the lowest cost?

## 3. Operating rules for every matchup

### 3.1 Immediate-threat priority

Use the following order. Within the same priority, compare legal candidates by expected survival and strategic value.

1. A lethal attack already reaching a base or production line, worker massacre, or cloaked unit without detection.
2. An existing supply block, a stopped critical production building, or a construction-position problem that requires cancellation.
3. The minimum defense for a confirmed enemy technology: detection, wall, Bunker, Photon Cannon, Sunken, Siege, splash, or anti-air.
4. Worker production and basic income, followed by continuous production from existing buildings.
5. The core technology, upgrades, and expansion of the current build.
6. Scouting that reduces a meaningful uncertainty.
7. A high-probability pressure, harassment, expansion, or follow-up production choice.
8. Preserving a valid existing order with wait.

Reserve supply buildings early enough to prevent a block, but do not use a rule that ignores a lethal attack just to build supply. Secure the minimum defense, army, or detection needed to survive the attack, then schedule the next supply block prevention.

### 3.2 Economy and production

- Keep worker production continuous whenever possible. Temporarily stopping workers is acceptable only when the state explains the need to survive an immediate all-in or to fund a critical defense, expansion, or technology.
- Worker saturation depends on patch count and travel distance. A useful reference is about two workers per mineral patch and up to three workers per gas geyser, but never enforce those as absolute numbers. Use actual income and travel distance. Never use a rule such as eighteen workers per mineral patch.
- A Drone, SCV, or Probe that becomes a building is no longer counted as an economic worker. Reflect that loss after an expansion, gas, tech building, or defensive building starts.
- When resources float, identify the current bottleneck among production, expansion, technology, defense, and upgrades. Do not lock all resources into one unit type while supply, detection, or defense collapses.
- Respect the number of simultaneous production queues and do not issue duplicate commands that cancel valid production.
- Distinguish between too few production buildings, insufficient resources for existing production, and resources blocked by supply, technology, construction slots, or power.
- An expansion increases income but also increases the area that must be defended. Evaluate attack timing, army, and scouting together.

### 3.3 Scouting

- Early worker scouting should check the enemy natural, first production building, gas timing, additional production, fast tech buildings, and initial army count.
- If a scout dies, retain what it actually saw as a fact, but do not extend that fact into the unseen future.
- An unseen natural is one signal, not a conclusion. It can mean a one-base attack, hidden expansion, fast technology, proxy, or delayed expansion. Choose the next observation that best separates these possibilities.
- Early gas does not automatically mean a tech all-in. Combine gas timing with gas amount, tech buildings, army, and expansion timing.
- The absence of a building is evidence only when the relevant area was actually explored.
- If new scouting contradicts the previous hypothesis, revise the build immediately. Do not ignore new facts to preserve the old build.
- When possible, send scouts to the location with the highest information value. A natural expansion, primary production line, or tech position is more valuable than repeatedly checking a known empty area.

### 3.4 Defense and combat

- Avoid fights without vision, cloaked engagements without detection, and frontal attacks into Siege, Photon Cannon, Sunken, or high ground.
- Walls and chokes buy time. Keep the defensive line coherent, preserve a repair target and a retreat path, and do not place all units outside the wall where the enemy can bypass it.
- Judge engagements using composition, upgrades, range, terrain, vision, splash, spells, and reinforcing units, not unit count alone.
- Do not send units in a long thin line. Whenever possible, make the attack arrive together and preserve a route to regroup and retreat. A divided squad that can die one group at a time should regroup.
- Do not automatically retreat only because local army count is lower than the confirmed enemy count. Conversely, do not fight just because numerical count is higher if Siege, Reaver, splash, terrain, or missing detection makes the fight unfavorable.
- As a conservative heuristic, if local combat power is below roughly 60% of the enemy's and there is no terrain, upgrade, or spell advantage, or if vision/detection is missing, prefer retreat, regroup, or scouting. This is a heuristic, not a law; composition and terrain must modify it.
- Re-evaluate an attack after it reaches the target. If the enemy is larger than expected, the target is behind static defense, or the army split, do not continue a suicide attack.
- Compare worker damage against the risk of losing the main army during a raid. If there is no exit path or the enemy army is joining, choose an exit.

### 3.5 Common game phases

- Opening: scout, establish basic production, prevent supply blocks, and identify expansion, technology, or attack. The 0-5 minute range is only a reference. In ZvZ, PvP, 9 Pool, and 2 Gate openings, early combat and survival can take priority over expansion.
- Mid game: verify the first strategy's result and add secondary production, upgrades, expansion, and detection. The 5-12 minute range also varies by matchup and map; do not use it as a fixed clock rule.
- Late game: compare expansions, production scale, upgrades, advanced technology, and army roles. Manage vision, expansions, harassment, and reinforcement rather than relying on one decisive fight. Do not switch automatically to a late-game composition merely because the game passed 12 minutes.

## 4. Race fundamentals

### Terran

- Keep SCV production active and reserve Supply Depots before the cap. Place Depots with the entrance wall, production path, and repairable defensive line in mind.
- Add Barracks, Factories, and Starports according to scouting and resources. Do not add production buildings while supply, gas, or upgrades cannot support them.
- A Bunker is a cost for a confirmed early Ling, Zealot, or Marine threat. Do not greedily land a natural Command Center until it can be defended.
- Tanks need Siege Mode, vision, high ground, and reinforcing units. Do not send one Tank forward where melee units can surround it.
- Bio should be evaluated as Marine, Medic, Stim, range, attack upgrades, and reinforcement production together. Preserve detection against Lurker, Reaver, Siege, and Dark Templar threats.
- Mech uses Vulture Mines to constrain movement and Tanks to hold locations. Add Goliaths, Science Vessels, or Wraiths from scouting rather than as automatic steps.
- Comsat Scan is for Lurker, Dark Templar, cloaked Wraith, and hidden-expansion information. When a cloaked threat is plausible, detection and scanning outrank a blind attack.
- SCV repair is valuable during combat, but compare it against the risk of losing every repair worker and collapsing the economy.

### Zerg

- Decide how Larva will be spent. Drones improve economy but reduce immediate defense; Lings, Hydras, Mutalisks, and Lurkers provide pressure or defense but delay economy and use gas.
- Prepare Overlords slightly ahead of supply and use them for vision, scouting, and detection. Spread them so Corsairs, Valkyries, and splash do not kill them together.
- A Drone morphed into a building leaves the economy. Account for the worker loss after a Hatchery, gas, Spawning Pool, or Hydra Den starts.
- Separate the roles of main, natural, and production Hatcheries. If early pressure is confirmed, a Sunken, Ling, and scouting can take priority over a third Hatchery.
- Use Creep, Sunken, Spore, and defensive terrain. Do not duplicate defensive buildings at every base without evidence of the corresponding air or ground threat.
- Lair, Spire, Hydra Den, Lurker Aspect, Hive, and Defiler cannot all be started at once. Compare enemy technology, available gas, expansion count, and transition cost.
- Defiler is a late-game core. Prepare Dark Swarm and Plague use, accompanying units, and survival while energy is being accumulated.

### Protoss

- Keep Probe production and Pylon power active. A Pylon block stops both production and building function, so do not delay it while watching only current supply.
- Gateway, Cybernetics Core, Robotics Facility, Citadel, Templar Archives, and Observatory order depends on the enemy pressure and selected technology.
- Dragoons depend on range and position. Do not let them get surrounded without high ground, choke, or vision.
- Shuttle and Reaver can create decisive damage but need a route, support, and a way home. Do not fly into unseen anti-air, Scourge, or Wraiths.
- If choosing Dark Templar, check when the enemy gets detection. Once Comsat, Overlord, Observer, or Spore is confirmed, do not repeat the same assassination pattern.
- Cannons buy time against confirmed early pressure. Do not overbuild Cannons in the main and destroy your economy and tech when the threat is not real.
- Observers detect Dark Templar, Lurker, Mines, Siege positions, and cloaked Wraiths. Do not redirect all production to Observers when the opponent has no plausible cloaked threat.

## 5. Matchup reference builds and branches

The following are representative templates for practice and decision making. Do not label one of them as universally strongest or always correct. Re-evaluate through scouting and legal candidates. Supply notation means approximate human supply immediately before an action. Code must validate actual costs, supply, and the available cap.

### 5.1 TvZ: 1 Rax FE into Bio and SK follow-up

#### Strategic purpose

Use early Marines, a wall, and a Bunker to survive Zerg's fast Ling pressure, then take an early natural Command Center and split into five-Rax Bio, fast Starport/Vessel, or Mech according to whether the Zerg is showing 2 Hatch Muta, 3 Hatch Lurker, 3 Hatch Hydra, or a fast all-in. Taking a natural does not mean stopping pressure or scouting. Confirm the Zerg's third Hatchery, gas, and tech.

#### Reference build: safe 1 Rax FE

1. 9/10 Supply Depot. Place it with the entrance wall and production path in mind.
2. 11/18 Barracks. Send an SCV to scout.
3. If the opponent appears to be Hatch-first or has a slow Pool, consider the natural Command Center around 15-18 supply. If 9 Pool, fast Lings, or an unknown position makes it unsafe, delay the CC and defend.
4. Use the first Marine for scouting and entrance defense. Schedule the second Depot and the CC together so the natural transition does not cause a supply block.
5. Once the natural CC is safe, add Refinery, a second Barracks, and Academy in an order supported by resources. Academy too late loses to Lurker or Muta; too much early gas can leave too few Marines.
6. Produce Marine and Medic continuously from two to five Barracks. Match Infantry Weapons, Stim, and Medic technology to the reinforcement count.
7. Bring Factory, Starport, and Science Facility forward when the opponent shows evidence of 2 Hatch Muta, Lurker, Defiler, or mass air. Select Science Vessel and Irradiate from that evidence.
8. If the opponent has three Hatcheries and little early army, use a +1 five-Rax Bio pressure to delay the third base and technology. Do not throw units into Sunken, Lurker, or Siege lines. Prepare a third CC and more production while applying pressure.

#### Scouting branches

- 12 Hatch or slow 3 Hatch: keep the 15-18 CC and second Barracks line, and bring forward the +1 Marine-Medic timing. If the opponent stays defensive, choose between third CC, more Barracks, and Starport according to the current bottleneck.
- 9 Pool or four to six fast Lings: if the natural CC has started, prioritize Bunker, SCV repair, and the wall. Do not send every Marine outside to protect the CC. If Lair is abnormally late after Pool and Extractor, increase the probability of a Speedling all-in and keep the wall and Bunker ready.
- 2 Hatch Muta signs: if there is early gas, only two Hatcheries, a delayed third Hatchery, Lair/Spire, or a large Overlord movement, combine Academy, Engineering Bay, Turret, and Starport/Vessel. Do not hard-code a Turret count; use the actual Muta entry routes and Scan access.
- 3 Hatch Hydra or Lurker signs: bring Marine, Medic, Academy, and Comsat forward and consider Factory Tank/Siege or Vessel. Do not advance in one line without knowing Lurker positions.
- The natural is unseen: keep the hypotheses of one-base Lurker, fast Muta, Ling all-in, and hidden expansion. Choose the highest-value SCV, Scan, or Marine scout and temporarily delay the CC or additional Barracks if needed.
- A fast third Hatchery with little attacking army: respect the economic investment. Use +1 pressure only to delay expansion or tech. If losses accumulate against Sunken or Lurker, retreat and transition to third CC, Vessel, and more production.

#### Operating checkpoints

- After taking the natural, did Depot, SCV, and Marine production remain active?
- Is there detection for Muta, Lurker, and Defiler-related threats?
- Are there enough Academy, gas, and upgrades to support five Barracks, or is the plan floating a production count it cannot pay for?
- If the opponent takes a third Hatchery while my army is moving, can the pressure actually cancel or delay it, or is this only a suicide attack?
- If Bio fails, is there a legal follow-up into Tank/Vessel, Mech, or a defensive expansion?

### 5.2 TvP: 1 Factory Siege Expand into two-base Mech

#### Strategic purpose

Use Tank and Siege to absorb early Dragoon and Zealot pressure while taking the natural, then use two-base Vulture, Spider Mine, and Tank production to pressure Protoss expansions, Shuttle, and Reaver. Do not lock the CC before identifying 2 Gate, Dark Templar, Robo/Reaver, fast Nexus, or Carrier tech.

#### Reference build: 1 Factory Siege FE

1. 9/10 Supply Depot.
2. 11/18 Barracks. Send an SCV to check the first Gateway, gas, Nexus, and additional production.
3. 12/18 Refinery.
4. Around 16/18, prepare the second Depot and a Marine. Adjust Marine count to the wall and the opponent's first army.
5. When 100 gas is available, start the Factory around 17/18. Gas timing changes with worker losses and mining.
6. If early pressure is weak enough to hold with the first Tank and Siege, start the second Command Center around 22/26 on safe high ground. If two-Gate units are arriving or scouting was cut off, delay the CC and secure Tank, Bunker, and Marine first.
7. Add the Machine Shop and prepare the first Tank and Siege Mode. Keep the Tank at the entrance, high ground, or natural defense line with vision; do not abandon one Tank in an unseen forward position.
8. Add Academy and Comsat to find Dark Templar, hidden technology, and expansions. Place Turrets according to real entry routes and detection gaps.
9. Produce Vultures and Tanks continuously from two Factories and grow to three to six Factories according to the ground army. Use Spider Mines to control approaches, expansions, and drop paths.
10. If Protoss takes a fast Nexus, consider a two-to-four Factory Vulture/Tank pressure. If the defense is established, do not force a frontal break; add third CC, production, and Armory upgrades.

#### Scouting branches

- 2 Gate Dragoon/Zealot pressure: delay the CC and prioritize Bunker, a second Tank, Siege, and Marine. Use SCV repair and terrain to buy time if the wall or ramp is threatened.
- Robotics Facility/Reaver: protect Tanks from Shuttle landing zones and use Mines, Turrets, and Marines to see Shuttle routes. Do not place all SCVs at the natural while the Shuttle is unaccounted for.
- Dark Templar: Academy, Comsat, and Turret are the priority. Do not spend Scan energy on attack scouting while detection is missing. Keep SCVs and Tanks shallow until detection is secure.
- Forge Fast Expand or fast Nexus: if the second base is confirmed and under-defended, prepare two-to-four Factory Vulture/Tank pressure with Mines. If the pressure is stopped, transition to Mines, third CC, and upgrades.
- Fast Carrier or air technology: select Armory, Goliath, air detection, or additional Starport from the legal candidates. Do not produce only Goliaths before air units are confirmed and lose ground strength.
- Natural unseen: retain 2 Gate, Dark Templar, Robo, proxy, and hidden Nexus hypotheses. Use Factory, Siege, and Comsat to survive and gather information.

#### Operating checkpoints

- Is the natural CC safe before the first Tank and Siege, or is it greedily exposed to a two-Gate attack?
- Are Mines controlling routes, or can the opponent simply walk around them because vision is missing?
- Are Academy and Comsat late enough that Dark Templar or a hidden expansion cannot be found?
- Do Depot, SCV, and gas income support two-base production?
- Are Vultures dying to Dragoons, Reavers, or Zealots without being replaced?

### 5.3 TvT: Machine Shop first with conditional one-Factory branches

#### Strategic purpose

TvT is an information and position battle among Tanks, Vultures, and Wraiths. Expanding before Machine Shop can be vulnerable to 2 Factory Vulture or Wraith pressure, so first secure the Factory and scout. Then split into one-Factory expand, two-Factory Vulture, one-Factory/one-Starport, or two-Starport Wraith according to evidence.

#### Reference build

1. 9/10 Supply Depot.
2. 11 Barracks and an 11-12 Refinery line. Send an SCV to check the entrance, Factory, extra gas, and expansion.
3. Build the second Depot and start the Factory at 100 gas.
4. Add the Machine Shop and prepare the first Vulture or Tank. Do not lock the natural CC before seeing the opponent's first unit and Factory information.
5. If safe, add the natural CC after Machine Shop and then the second Factory. If pressure is visible, choose the second Factory, Tank, Vulture, or Bunker instead of the CC.
6. If scouting supports one-Factory/one-Starport or two-Starport Wraith, choose Starport, Control Tower, Wraith, Goliath, Turret, or Comsat as needed. Dropship is not a default step; choose it only after vision and anti-air are understood.
7. Place Spider Mines at entrances, expansions, Tank approaches, and drop routes. Siege Tanks only with vision and high ground. Never send Vultures blindly into the range of sieged Tanks.

#### Scouting branches

- Enemy two Factories/Vultures: delay the natural and defend with four to six Vultures, Mines, and Tanks. Choose the CC only after checking whether the opponent also transitions to an expansion.
- Enemy one Factory/one Starport Wraith: if the Starport is early, combine Wraith, Goliath, Comsat, and Turret. A ground advantage is not enough if air vision is lost and the natural or supply line is exposed.
- Enemy two Starport Wraith: prioritize anti-air and production over expansion. Check Wraith count and Cloak; forbid forward movement without detection.
- Enemy fast natural: use a Tank/Vulture timing to pressure it, but do not enter a defended Siege line. Match the economy with my own CC and second Factory.
- Enemy Factory unseen: increase the probability of proxy Factory, hidden Factory, or fast Starport. Scout with SCV and Marine and keep vision over the entrance and construction space.

#### Operating checkpoints

- Can my Tanks see and fire on enemy Tanks first?
- Were Mine locations revealed or cleared?
- Am I sacrificing air vision by moving on the ground without knowing the enemy Wraith count?
- Does the natural CC have army, repair, and detection to defend it?

### 5.4 ZvP: 3 Hatch Hydra or 3 Base Spire into Lurker and Defiler

#### Strategic purpose

Distinguish Protoss Forge Fast Expand, two-Gate pressure, Corsair, Dark Templar, and Robo/Reaver before combining a safe third Hatchery with Lair, Spire, or Hydra Den. 3 Hatch Hydra, 3 Base Spire, 5 Hatch Hydra, and Lurker/Defiler are different branches; do not combine them into one fixed build.

#### Reference build: safe three-base opening

1. Around 9/9, build an Overlord. Send it toward the Protoss entrance, gas, and natural.
2. Around 12/18, start the natural Hatchery. If two-Gate pressure is already strong, Pool, Lings, and defense can take priority over the third Hatchery.
3. Around 11/18, start the Spawning Pool. The order can shift with map and Drone timing, so actual resources and enemy army take priority over supply numbers.
4. When the Pool completes, make at least two pairs of Zerglings to check the entrance and expansion. Adjust Ling count to the number of Gates, Zealots, and Dragoons.
5. If Protoss is Forge Fast Expanding with little early army, consider the third Hatchery around 13/18. If two Gates are confirmed, delay the third Hatch and use Sunken, additional Lings, and scouting to survive.
6. Use an Extractor around 14-16 supply as an approximate gas line for Lair and follow-up technology. Adjust gas to Drone losses, Ling count, and scouting.
7. After Lair, choose Spire or Hydra Den. Spire is an option when Corsairs are limited and Protoss is investing in expansion or ground technology. Hydra Den supports defense against two-Gate or Robo pressure and a Lurker transition.
8. For 3 Hatch Hydra, combine Hydralisk Range, Hydra production, and more Hatcheries. Do not suicide the first few Hydras into Cannons and Dragoons. Add Lurker Aspect and Overlord Speed when the state justifies them.
9. For 3 Base Spire, coordinate Spire, Overlord placement, Scourge, and the fifth Hatchery with Corsair and ground army information. 5 Hatch Hydra is an economic ground transition after immediate pressure is ruled out.
10. In mid and late game, combine Hive, Defiler, Lurker, Hydra, and Ling and use fourth and fifth bases and upgrades according to Protoss expansion count and army. Do not start Hive or Ultralisk automatically.

#### Scouting branches

- Forge Fast Expand: third Hatch, Drones, Spire, or Hydra can be accelerated. If Cannons and Corsairs are confirmed, spread Overlords and prepare Scourge/Hydra.
- Two-Gate pressure: Lings, Sunken, Hydra, and scouting can come before the third Hatch and gas. Do not keep making Drones while both Gates continue producing.
- Fast Dark Templar: secure at least one detection layer from Lair, Overlord Speed, Spore, and Overlord coverage. Defend the actual routes into main, natural, and production Hatcheries.
- Multiple Corsairs: spread Overlords and use four to eight Scourge, Hydras, or Spores for vision and transport/scout protection. Do not place Spores mechanically at every base.
- Robo/Reaver: do not line Drones and Overlords together. Use Hydra, Scourge, vision, and flanks to restrict Shuttle landings. Do not enter directly under Cannon/Reaver.
- Protoss natural unseen: preserve the possibilities of one-base two-Gate, Dark Templar, Robo, and hidden expansion. Do not automatically add three Hatcheries; choose among Ling, Overlord, and Drone scouting.

#### Operating checkpoints

- Is the third Hatchery a defensible economic investment or an unprotected greed choice?
- Is there detection and vision for Corsair, Dark Templar, and Reaver?
- Are Lurkers being teched before enough Hydra or support exists, locking gas and time without a purpose?
- What role does each Muta, Scourge, and Hydra group have against the observed ground and air army?
- Are Lurker/Hydra/Ling plus Defiler, reinforcement Hatcheries, and fourth/fifth bases connected into one viable transition?

### 5.5 ZvZ: 9 Pool Speed, 12 Pool, 12 Hatch, and the Mutalisk race

#### Strategic purpose

ZvZ has no single opening that is safe on every map against every scout. 9 Pool Speed, Overpool, 12 Pool, and 12 Hatch must be selected from the opponent's Pool, Gas, Hatchery, and Ling count. If the first Lings are lost, a fast Mutalisk tech does not save the base, so calculate survival before economy.

#### Reference build A: 9 Pool Speed pressure into 2 Hatch Muta

1. 9 Overlord. Check the enemy main direction and natural.
2. Use a 9 Pool line. Keep the first Lings alive long enough to determine whether the opponent opened natural Hatchery or 9 Pool.
3. Use a 10-12 Extractor line to fund Metabolic Boost and Lair.
4. After Pool completes, make Lings according to the enemy's actual count. Pressure a natural Hatchery against a greedy opening; if the enemy has many Lings, make Lings, Sunken, and a defensive position instead of Drones.
5. If pressure succeeded and the opponent is not making many additional Lings, add the second Hatchery in a defensible position and prepare Lair. Against a 9 Pool that is still producing, Lings and defense come before the second Hatchery.
6. After Lair completes, start Spire and prepare Scourge and Mutalisks together. The first Muta count depends on resources, enemy Scourge, and my production Hatchery; six to eleven is a target range, not a fixed command.
7. Use Muta control to pressure Overlords, Drones, and Scourge, but do not enter deep into the main without knowing the enemy Muta count and exit path. Consider third Hatchery and additional gas after air control is confirmed.

#### Reference build B: 12 Pool or 12 Hatch variation

1. After 9 Overlord, choose 12 Pool or 12 Hatch. Supply changes with Drone production and Overlord completion.
2. For 12 Pool, use 12 Extractor and ten to twelve Lings to stabilize the opening, then choose the natural Hatchery after seeing the opponent's Hatchery and Pool. Do not interpret 12 Pool as automatically economic.
3. For 12 Hatch, require evidence that the opponent is not 9 Pool or that enough Ling defense exists. If 9 Pool appears, make Lings and defense instead of Drones.
4. Compare both players' first Gas, Lair, and Spire timing. Add a second gas and another Hatchery only when Mutalisk production can be supported.

#### Scouting branches

- Enemy 9 Pool: stop increasing Drones, match Lings, and prepare Sunken, wall, and retreat routes. If the first army has not decreased, delay Hatchery and Lair.
- Enemy 12 Hatch: use Ling scouting to pressure and compare the Mutalisk race. If the opponent makes many Drones, adjust second Hatchery, Gas, and Lair; do not force a bad Ling fight.
- Enemy fast Spire: secure Scourge and defensive Mutalisks and spread Overlords. Do not send my Mutalisks separately without knowing the enemy air count.
- Enemy continuous Ling production: delay Mutalisk technology when necessary and prepare Sunken, Lings, and a defensive position. Do not lock Drones and Gas while the natural cannot survive.
- Missing enemy Overlords: increase the probability of hidden Muta or Scourge and secure vision around movement routes and the main perimeter.

#### Operating checkpoints

- What were the actual losses in the first Ling fight, and how many units remain?
- Is my Spire faster while the opponent has more Lings or Scourge?
- Can my Mutalisks be trapped by Scourge, Turret, or Spore?
- Have I invested too much in third Hatchery or Drones before confirming air control?

### 5.6 PvP: 3 Gate Robo baseline and 2 Gate Reaver pressure

#### Strategic purpose

PvP is an early information battle among one-base technology, two-Gate pressure, four-Gate Dragoon, Dark Templar, and Fast Expand. A stable baseline is 3 Gate Robo; the faster pressure option is 2 Gate Reaver. Choose the natural only after the first engagement and enemy technology are understood.

#### Reference build A: 3 Gate Robo

1. 8/9 Pylon. Send the first Probe to check the entrance, gas, and proxy locations.
2. 10 Gateway.
3. 12 Gas.
4. 14 Cybernetics Core and a Zealot or first defensive unit. If two Gates are plausible, increase the value of Zealot, wall, and Probe block.
5. Around 18, make the first Dragoon; around 20, take Dragoon Range or its legal equivalent according to resources.
6. Around 26, start Robotics Facility. If the first Dragoon or Probe losses delay it, secure defense and production rather than forcing the exact number.
7. Around 29, add the Gateway that creates three-Gate production. Around 33, prepare Observatory if Dark Templar, hidden technology, Mines, or vision loss is plausible.
8. Use Dragoon and Zealot to hold the entrance and natural while the Observer identifies enemy Robo, Dark Templar, and expansion. If Protoss fast expands with little army, choose 3 Gate pressure or my own natural.

#### Reference build B: 2 Gate Reaver

1. 8/9 Pylon, 10 Gateway, 12 Gas, and a 14 Core line.
2. Add the second Gateway early and use Zealot/Dragoon to hold the entrance.
3. Around 25 supply, start Robotics Facility and then Support Bay, Shuttle, and Reaver. Exact supply changes with the first Zealot, Dragoon, and Probe losses.
4. Use Shuttle/Reaver against workers and production, but do not fly deep without knowing anti-air, pursuit units, and the route home.
5. While Reaver creates time, compare second Gateway production, Observer, and Natural according to the current threat.

#### Scouting branches

- Enemy two Gates: do not rush Robo while ignoring the entrance. Secure Dragoon, Zealot, Probe block, and high-ground defense. Do not take a natural while the first army remains unaccounted for.
- Enemy one-Gate Robo: use Observer and Robo information to identify Reaver timing and compare Dragoon, Reaver, and expansion timing.
- Enemy Dark Templar: secure detection through Forge/Cannon, Observer, or scouting candidates. Do not attack the enemy main without detection.
- Enemy four-Gate Dragoon: use Reaver, high ground, choke, and Gateway count. Do not be surrounded outside the narrow entrance.
- Enemy Fast Expand: if low early army is confirmed, use 2 Gate Reaver or 3 Gate pressure to delay the expansion, or match it with my own expansion.
- Proxy or unseen enemy Gate: keep Probe and Zealot scouting, delay the natural, and defend the main entrance.

#### Operating checkpoints

- Will my first Dragoon and Range be later than the first enemy pressure?
- If Robo is complete, is there an escort and return route for Shuttle/Reaver?
- Is there a plausible Dark Templar threat without Observer, Cannon, or another detection layer?
- Are three Gates causing Probe, Dragoon, or Pylon blocks because the economy cannot support them?
- Before a major fight, have I checked the enemy's second tech building, Gateway count, and expansion?

## 6. How to execute a build order

Treat every reference build as five parts:

1. Goal: for example, “take the natural, then use a +1 five-Rax pressure to delay Zerg's third Hatchery.”
2. Required conditions: for example, “9 Pool has not been confirmed, and the first Marine, wall, and SCVs can defend the natural CC.”
3. Variable steps: for example, “Academy, Factory, and Starport order changes with Muta, Lurker, and three-Hatch evidence.”
4. Cancellation condition: for example, “if two-Gate units arrive or Dark Templar is plausible, choose Tank, Bunker, or Comsat before the CC.”
5. Next re-evaluation: re-evaluate after new scouting, completion of the first production unit, or confirmation of enemy expansion, technology, or attack.

When a build is late, apply these rules:

- Find a legal candidate that preserves the core purpose. For example, if five-Rax pressure is late, reduce it to three-Rax +1 while adding a third CC and keeping the economy viable.
- Treat completed buildings and spent resources as irreversible state and find the best continuation from there.
- Delay nonessential technology, upgrades, or attacks to fund required defense, supply, and production.
- If the opponent countered the build, stop executing its later steps automatically. Recompute from the opponent's revealed weakness and my survival chances.
- After an attack fails, do not send the same army back immediately. Compare losses, enemy reinforcements, my production, and vision before choosing regroup, expansion, technology, or another pressure.
- If the observation is stale or candidates are insufficient, use lower confidence and choose safe scouting, defense, or wait.

## 7. Jev Choice response rules

- Read each question's `criteria` as the meaning of the actual candidate. Do not infer an unavailable command from its label.
- At `domain`, choose among economy, production, construction, attack, defense, scout, and wait according to the risk that needs solving now.
- At an intermediate group, compare candidates within the already selected domain and producer, squad, or construction purpose.
- At a leaf, verify that the real ID, command target, unit, resource, and location match the current state.
- When economy and attack candidates coexist without an immediate threat, favor idle workers, supply, and production bottlenecks. If an attack is reaching the base or can reliably cancel an expansion, reconsider the expected value of defense and attack.
- `probabilities` must include every current option and sum to one. Do not assign zero to an option merely because it is unfamiliar; if the state does not distinguish options, spread the distribution.
- `confidence` describes how strongly the current evidence supports the choice, not an unsupported guarantee that the choice is correct. Lower it when scouting is cut off or the enemy position is only a hypothesis.
- Do not create a separate natural-language action for the rationale. Return the requested Choice format only. If explanations are needed, code should log node, choice, confidence, and observed frame.

## 8. Judgments to avoid

- A single rule such as “if current supply plus four is near the cap, always build supply before any attack.”
- Certainty such as “unseen technology is always the highest threat” or “no natural means a 100% all-in.”
- A universal matchup clock such as “all races expand first from 0 to 5 minutes” or “everything after 12 minutes is automatically late game.”
- A single combat number such as “retreat whenever my army is 60% of the enemy,” ignoring composition, terrain, and upgrades.
- Brood War-incompatible elements such as Orbital, Medivac, Marauder, a StarCraft II Queen, or Chrono Boost.
- Selecting a unit or building from a matchup that the current action candidates and state do not support.
- Treating an old last-seen position as the enemy's current position or treating a `start` label as a confirmed enemy main.

## 9. Current implementation scope

This policy contains strategic references for all six matchups, but the current MingleCraft v0.1 execution contract is limited to a Terran-versus-Terran Observation and an ActionGenerator centered on SCVs, Marines, Supply Depots, and Barracks. Therefore, the current runtime may execute only the TvT decisions represented by its actual candidates. To activate TvZ, TvP, ZvP, ZvZ, and PvP, extend race fields, units, buildings, technology, expansions, upgrades, race-specific action generation, legality validation, and BWAPI command mapping together. Never invent a unit or command from this policy merely because the strategy section mentions it.

Always validate the final choice in this order: immediate lethal threat -> facts versus hypotheses -> legal candidates -> required build conditions -> supply, production, and detection -> the lowest-risk choice that preserves the goal -> re-evaluation at the next observation frame.
```

## Original full-context architecture (reference only)

For an eligible Jev step, runtime performs two sequential calls:

1. Build the value request with the current `observation`, all currently legal `candidate_actions`, the complete cumulative `match_history`, the shared `strategy_policy`, and prior estimates. Ask one Noul question, `win_probability`.
2. Record the fresh value estimate in history.
3. Build the policy request with the same current observation and candidate set, the complete history including that fresh estimate as `latest_value`, and the shared strategy. Ask the Choice hierarchy.
4. Resolve only an existing action ID. Code owns command construction, legality checks, expiry, and execution.

The second call is sequential because the policy must consume the fresh value estimate from the first call. Both stages use the same Jev model/provider configuration. If the value stage succeeds and policy fails, the value result and failure are logged; the decision falls back to `wait`. A global per-step deadline covers both stages.

Baselines may continue to use their existing one-stage behavior. The demo remains rule based; the live `serve` default is Jev when the runtime implementation enables that default. No live API key or live response is assumed by this document.

## Full timestamped local match history

History is cumulative for one match and resets at match boundaries. Every event has:

```json
{"timestamp":{"frame":123,"game_seconds":5.125}}
```

The current implementation uses these event classes:

- `observation`: a timestamped observation snapshot. Hidden enemies are filtered out before it enters model-facing history. Observation fields are interpreted as a top-level delta when the runtime supplies deltas: unchanged fields carry forward, and a replaced list replaces its prior list.
- `issued_action`: the action ID and action payload selected by the harness. This records intent only; it does not prove that a BWAPI command completed.
- `command_receipt`: a deduplicated receipt keyed by `decision_id`, with attempted/accepted counts and a reason. It means command acceptance or rejection at the bridge, not completion in the game. Effects must be inferred only from later observations.
- `value_estimate`: the prior Noul result at its timestamp. It is an earlier model estimate, not fresh scouting, ground truth, an independent label, or a measured win rate.
- `match_end`: the final observation and result when the match ends.

Missing observations are not negative evidence. A visible enemy is a fact only at the timestamp where it was visible; last-seen state is dated and may be stale. A public start location remains a hypothesis. Repeated receipt windows are deduplicated by `decision_id`.

The current observation is supplied to the model; cumulative history remains local and is not a replacement for it. The value stage must reassess from observations rather than anchor on `latest_value` or older estimates. The policy stage may use the fresh `latest_value` as one signal, while still obeying immediate-threat, legality, uncertainty, and candidate rules.

## Candidates, prompts, and safety boundary

The generator is the authority on executable candidates. For Jev, the runtime sends the complete supported legal candidate set for the current observation, without heuristic pruning; the finite set may therefore exceed the baseline cap of 50. The policy may select only an option ID already present in the request. It never emits a free-form command or target.

The shared strategy may describe all six matchups, but the current BWAPI contract remains limited to the fields, races, units, buildings, and commands actually implemented by the runtime. A strategy paragraph does not activate unsupported game objects.

## Performance and evidence limits

The six-frame schedule is a cadence target corresponding to 2–4 decision cycles per second under favorable conditions, not a verified live result. Full history increases request size, latency, and context use as a match grows. Sampled observations cannot capture events between frames. Receipts show bridge acceptance, not completion. The native integration is restricted to the supported TvT/BWAPI contract and is not a complete BWAPI bot.

No statement in this document claims empirical calibration, measured win-rate accuracy, live API compatibility, or competitive performance.

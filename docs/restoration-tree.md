# Restoration tree (`tree-v1`)

The restoration engine (`concierge/restoration`, spec-03) schedules repairs on a tree of line devices. This page
records what the data looks like, how a node gets its level, and what the artifact contains. The builder is
`concierge/curate/restoration_tree.py`.

## What `device_dim` contains (checked 2026-10-24, 32,600 devices, 110 circuits)

| `device_class` | `device_type` | devices | `is_protective` | depth range | has children |
| --- | --- | --- | --- | --- | --- |
| `breaker` | Circuit Breaker | 110 | no | 0 | yes (the root of each circuit) |
| `isolating_device` | Fuse | 6,757 | yes | 1 to 11 | yes |
| `isolating_device` | Recloser (Hydraulic) | 286 | yes | 1 to 9 | yes |
| `isolating_device` | Recloser (Electronic) | 36 | yes | 1 to 3 | yes |
| `isolating_device` | Sectionalizer | 1 | yes | 2 | yes |
| `transformer` | Step | 108 | no | 1 to 7 | 107 of 108 |
| `transformer` | Distribution | 25,302 | no | 1 to 12 | never (always a leaf) |

- Every circuit has exactly one root (the breaker at depth 0). No parent sits on another circuit.
- Parents are always shallower than their children. `depth` and `parent_key` agree.
- `n_premises_served` counts every premise whose chain passes through the device. 26,019 of 32,600 devices serve
  fewer than 10 premises (`small_group`).
- 60 devices have more than one parent in the raw chains. `device_dim` keeps the majority parent, so the tree uses it.
  The 210 premises whose own chain differs are flagged `alternate_path`; their rows carry `ap = 1` and the response
  adds a note.

## Node-level rule

Each node gets one of three levels. A repair of an asset type lands on a node of the matching level
(`ASSET_LEVEL` in `jobs.py`: primary line, recloser, switch, capacitor bank and step transformer are `feeder`; fuse
bank and pole are `lateral`; transformer, secondary line and light are `service`).

| Level | Rule | Meaning |
| --- | --- | --- |
| `service` | `device_class == "transformer"` and `device_type != "Step"` | The distribution transformer and the secondary lines to the homes |
| `feeder` | any other device with `depth <= 1` | The trunk leaving the substation and the first protected sections |
| `lateral` | any other device with `depth >= 2` | Branches below the first protected sections |

Why these inputs: `device_class` and `device_type` identify the distribution transformers (the last device before
homes), and `depth` separates trunk from branch. `is_protective` was inspected and is not needed: every isolating
device is protective, and the only non-protective line device is the step transformer, which the depth rule already
places. The rule is an engineering assumption (a fuse at depth 1 is called "main line"), listed with the other
assumptions in `design.md`.

A level with no nodes on a circuit uses the next level up, and the breaker is always a feeder node, so every repair
has somewhere to land. After merging (below), 933 nodes are `feeder`, 2,367 `lateral` and 3,281 `service`. Six
circuits have no `lateral` node and five have no `service` node.

## Merging small groups

Devices that serve fewer than 10 premises are merged into their parent, so the artifact never describes a handful of
households. Because a parent serves at least as many premises as each child, a merged device is almost always a
whole subtree. The premises under it attach to the nearest surviving ancestor (`pn`). In the rare multi-parent case a
surviving node can have a merged parent; it is then re-attached to the nearest surviving ancestor, and depth in the
artifact is the depth in the merged tree.

A circuit whose breaker serves fewer than 10 premises is dropped, and its premises get no position (33 premises on
5 circuits in the 2026-10-24 data; the risk engine already returns a fallback for circuits that small). Rows without a
position use the three-level path of restoration-v1 and are flagged `model_path = "v1_levels"`.

Result on the 2026-10-24 data: 105 circuits, 6,581 nodes (of 32,600 devices), 117,566 of 117,599 in-topology premises
positioned, largest circuit 210 nodes, deepest node 9.

## Artifact: `serving/risk/restoration_tree.json`

```json
{"version": "tree-v1", "circuits": {"0": {"parent": [-1, 0, 0, 1], "depth": [0, 1, 1, 2],
  "served": [3100, 1800, 900, 40], "level": ["feeder", "feeder", "lateral", "service"]}}}
```

- Arrays only. No device keys, circuit labels, coordinates or addresses. The circuit index is the position of the
  circuit's breaker key in sorted order (a salted hash), so it says nothing about the circuit number.
- Node 0 is the breaker. A parent always has a lower index than its children. Every `served` is at least 10.
- Each scored premise row in `premise_risk.jsonl` carries `pc` (circuit index) and `pn` (node index). The engine uses
  them to find the customer's path (the parent chain from `pn`) and never returns them.
- The file lives next to `premise_risk.jsonl`, under a prefix CloudFront does not serve
  (`PUBLIC_STATIC_PATHS` in `infra/lib/stack.ts` lists only `serving/risk/calibration.json`).

## Build

```bash
CONCIERGE_TOKEN_SALT=<secret> <py> scripts/curate.py --no-drawings
CONCIERGE_TOKEN_SALT=<secret> <py> scripts/build_risk_artifacts.py     # also writes restoration_tree.json
```

`scripts/etl.py` calls the same function after the risk build and copies the tree into `clean/serving/risk/`.
Run `make load-table` afterwards so DynamoDB gets the `pc`, `pn` and `ap` fields.

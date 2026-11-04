# Data Integration and Configuration

[Home](../README.md) · [简体中文](DATA_AND_CONFIGURATION.zh-CN.md) · [Quick start](QUICKSTART.md)

<a id="formats"></a>
## Supported formats and existing configurations

Readers extract numbers, images, and timestamps from files. Robot configuration describes which part each recording belongs to, what it represents, and which units it uses. Choose a matching format and configuration, then check which algorithms can run on the data.

### Data formats

| Format | What can be read | Requirements |
|---|---|---|
| LeRobot v2.1 / v3.0 | Numeric columns, supported image columns, and associated videos | Keep metadata and indices describing each operation, including its video range; configure the meaning of each column. The official reader needs Python ≥3.12 and `.[lerobot]`; an alternative reader is provided by `.[formats]` |
| HDF5 | Numeric arrays and images | Specify the group for each operation, such as `data/demo_0`; one file may contain several operations. Configure array names, component order, and time information |
| Parquet | Numeric columns and supported image columns | Non-LeRobot data needs explicit start/end rows for each operation and a time column; one file must not automatically be treated as one operation |
| ROS1 / ROS2 bags | Joints, poses, IMU, images, and timestamps in supported messages | Requires `rosbags` and readable message definitions. Custom messages also need support for extracting the required fields |
| MCAP | Numbers, images, and timestamps in supported ROS1, ROS2 CDR, or Protobuf messages | Requires `mcap` and the corresponding decoding libraries. Message definitions must be available in the file; Protobuf needs an embedded descriptor. Unreadable content limits the corresponding checks |
| JSON with video directories | Per-record numeric values and camera videos, such as X1 Pro robot-bridge exports | JSON structure, data names, and video layout must match the reader; correspondence between numeric records and video frames must be specified |
| Standalone MP4 | Video frames and playback timestamps | Supports image/video checks. A video alone provides no joint positions, and playback time cannot establish acquisition synchronization |

See [quick start](QUICKSTART.md) for format dependencies and [`src/edc/data/readers/`](../src/edc/data/readers/) for implementations. Images and videos both support visual checks; checks for video-file damage require a video file.

### Existing robot configurations

These configurations describe specific data layouts. Match both the file format and data names; different recording setups for the same robot may require different configurations.

| Configuration | Matching data | Configured content and requirements |
|---|---|---|
| [X1 Pro · MCAP](../configs/robots/x1pro_mcap.yaml) | The MCAP recording used in quick start | Both arms' joints and end effectors, four color cameras, head depth, and other recordings. Joint position/velocity and end-effector XYZ units are declared; clock relationships, depth scale, and some numeric limits remain unconfirmed |
| [X1 Pro · JSON + video](../configs/robots/x1pro.yaml) | JSON with three MP4 files exported by robot-bridge | Both arms, master/follower recordings, and head/wrist cameras; numeric names include `follow_*` and `master_*`. Some units, including gripper units, are unknown. This configuration cannot directly replace the MCAP configuration |
| [Leju KUAVO 4pro](../configs/robots/leju_kuavo.yaml) | ROS1 bags from the dexterous-hand version | Both arms' joints, hands, IMU, and cameras; end-effector positions are calculated from joints and URDF. Depth scale is unconfirmed, so metric depth checks are unavailable |
| [Luoshi dual arm](../configs/robots/luoshi.yaml) | The LeRobot v3.0 layout described in the configuration | A 14-element joint array split into seven left and seven right joints, plus three color cameras. No measured end-effector positions; commands cannot substitute for feedback. Joint limits and speed bounds are absent |
| [pepsi_sort single arm](../configs/robots/pepsi_sort.yaml) | The LeRobot v3.0 layout described in the configuration | End-effector pose, gripper, and wrist camera. No joint angles, and gripper units are unknown. Frame-generated timestamps cannot support acquisition synchronization checks |
| [Franka Panda · simulation](../configs/robots/franka_panda.yaml) | robomimic/robosuite HDF5 data | Single-arm joints, end effector, and gripper; `data/demo_*` groups hold separate operations. This is simulation data, and its 20 Hz frame timeline is not recorded acquisition time |

These files describe how to read and check the corresponding data. They do not establish support for every recording from the same robot model. Algorithm availability still depends on the actual file contents and settings.

### What to verify before use

- **How is the data stored?** A bag/MCAP message is a timestamped record. Its message type describes the fields and their storage, for example `position` and `velocity` in a joint message. A custom structure needs a reader that can decode it and extract the numbers; recognizing the file extension is not enough.
- **What do the numbers represent?** Configuration must specify whether `position` is a joint angle or an end-effector position, whether it uses degrees, radians, or metres, whether `effort` is current or torque, and whether records are commands or measured feedback. These meanings cannot be inferred from field names alone.
- **Can the images and times support the selected check?** Images must decode into frames. Depth also needs a conversion to metres and rules for invalid measurements. Timestamps need a known meaning and unit. Comparing different devices also requires a confirmed relationship between their clocks; see [units and time](#units-time).

<a id="dataset"></a>
## 1. Inspect fields and episodes

```bash
python examples/quickstart/list_inputs.py /path/to/dataset/episode.mcap
```

This script uses the public IR. The current `edc channels` command fails on this MCAP with a missing `Episode.info`; this example avoids that command without changing the reader.

Use the reported topic/feature as `source`. Preserve field names and component order. A field called `effort` does not establish whether it is current or torque.

A dataset may contain many operations, each with joint, camera, and other recordings. For example, a collection of 100 grasp attempts can be checked one attempt at a time, with separate left-arm, right-arm, and camera data. In code, these are called `Dataset` (the collection), `Episode` (one operation), and `Channel` (one recorded stream).

The start and end of each operation come from the data's descriptions or explicit configuration, not file counts. LeRobot uses metadata. HDF5 can use `data.episode_group` to identify the group containing individual operations. A custom collection can list operations in `dataset.yaml`:

```yaml
schema_version: 1
id: my_task_dataset
version: '1'
episodes:
  - id: take_001
    path: episode.mcap
    format: mcap
    selector: {}
```

Paths are relative to the manifest. This example assumes the complete recording is one episode. Multi-episode sources require selectors supported by their reader. Do not infer episode count from file count. Keep sources read-only and write results elsewhere.

<a id="robot"></a>
## 2. Robot and algorithm configuration

Start from [single_arm.yaml](../configs/templates/single_arm.yaml), [dual_arm.yaml](../configs/templates/dual_arm.yaml), or [dual_arm_hand.yaml](../configs/templates/dual_arm_hand.yaml).

| Section | Responsibility |
|---|---|
| `robot` | Name, confirmation owner, signature; `name` begins with an ASCII letter and `confirmed_by` is required; `signature` is written by `edc batch` when it asks for a signature after the yaml changes, do not edit it by hand |
| `data` | Explicit time column, absent acquisition-time explanation, minimum duration, HDF5 episode group |
| `arms` | Actual arms, joint/end-effector feedback, commands, grippers/hands, Home, and optional URDF |
| `cameras` | Source, camera role, RGB/depth, and wrist ownership; `info` names the matching camera-info topic paired by the structure check |
| `imu` / `other` | IMU components and other declared numeric fields |
| `checks` | Algorithm parameters, channel selection, and scope for this run; `skip: [algorithm_id]` skips a check |

Example single-arm card; replace field names to match your data:

```yaml
robot:
  name: my_robot
  confirmed_by: data_owner
arms:
  main:
    joints: {source: observation.state, index: 0-5, unit: rad}
    eef_position: {source: observation.eef, index: 0-2, unit: m}
cameras:
  head: {source: observation.images.head, mount: head}
checks:
  Numeric_Check: {arms: all}
```

`source` identifies the recorded field; `field` selects a message member such as `position`; `index` selects components. `0-5` is inclusive; lists such as `[0, 2, 4]` are also supported. Joint declarations require explicit component counts. Derived logical names such as `joint/arm/main/feedback` bind algorithms to the declared signal; names cannot turn commands into feedback.

**Declare the robot separately from the run scope:**

| Declared arms | Algorithm parameter | Scope |
|---|---|---|
| `main` | `arms: all` | The single arm |
| `left`, `right` | `arms: all` | Both arms, checked separately |
| `left`, `right` | `arms: [left]` | Left arm and explicitly retained shared inputs |
| `left`, `right` | `arms: [right]` | Right arm and explicitly retained shared inputs |

For combined arrays, declare distinct indices; for separate arrays, use distinct sources. Fields covered by component views are not checked again as complete raw arrays. Missing arms or unknown ownership cannot silently become full single-arm coverage. [Scope details, Chinese](EVIDENCE_AND_SCOPE.md)

Reuse robot configuration across task datasets while keeping tasks and episode metadata with each dataset. Recheck source mapping, units, calibration, and clock applicability for each capture batch.

```bash
edc robot check --robot /path/to/robot.yaml --data /path/to/dataset/episode.mcap
```

This validates the card-style YAML above. Reading values does not establish their physical meaning. The quickstart [`x1pro_mcap.yaml`](../configs/robots/x1pro_mcap.yaml) is a complete profile with field-level confirmations and unknown clocks. Pass it directly to `open_dataset(profile=...)`, `run --robot`, or `batch --robot`; do not compile it again with `compile_card()`.

<a id="units-time"></a>
## 3. Units and time

### Units

Confirmed physical fields are read in SI **inside the official executor's algorithm context**. Examples: `90 deg → π/2 rad`, `1000 mm → 1 m`. Source arrays remain unchanged.

| Card field | Supported source units/representation | Algorithm representation |
|---|---|---|
| `joints` | `rad`, `deg` | `rad`; the primary arm card currently supports rotational joints |
| `joint_velocity` | `rad/s`, `deg/s` | `rad/s`; needs configured or URDF velocity limits |
| `eef_position` | `m`, `cm`, `mm` | `m` |
| `eef_orientation` | `quaternion` with `order: xyzw/wxyz`, or `rpy_rad/rpy_deg` | Explicitly normalized XYZW quaternion |
| Gripper/hand/`other` | Supported physical units, `raw`, or `unknown` | Physical, raw-range, or finite-value checks as applicable |
| IMU | Confirmed acceleration/angular-velocity units | `m/s^2`, `rad/s`; gravity semantics need separate declaration |

`raw` denotes nonphysical readings and requires a `range` in the card; `unknown` denotes missing knowledge, not dimensionless `1`. The current card rejects `joints.unit: unknown` and `m`. Unknown numeric fields can use supported `other` entries, but cannot thereby qualify for joint physical checks.

When the physical conversion is known, declare a unit and explicit `scale`, for example `unit: rad` with `scale: {raw: [0, 4095], value: [-3.1416, 3.1416]}`. Normalization does not establish units.

Standalone source reads from `open_dataset()` are not universally SI numeric views. Plugins should read from the executor-provided `context.episode` or `check()` episode. Use `inspect_recorded_signal()` for raw diagnostics. Conversion support does not yet cover every physical modality.

### Time

Distinguish acquisition, container, playback, synthetic, and unknown time. Unit conversion, clock mapping, and resampling have separate evidence.

- MCAP/bags may contain both message headers and container recording time; file format alone does not establish synchronization.
- LeRobot commonly has a shared row timeline. Bind original acquisition time only when actually recorded; shared row times cannot impersonate independent sensor timestamps.
- A card can bind a known source column with `data.time: {source: source_timestamp, unit: ns}`. Use `data.time_note` when acquisition time is absent. Do not label a generated index timeline as acquisition time.
- A clock name created by card compilation is not measured synchronization evidence. Verify the declaration before cross-channel comparison; preserve unknown clocks or explicit mappings in a complete profile where needed.
- Selecting the head camera as a reference does not confirm a common clock. See [algorithm-specific requirements](ALGORITHMS.md).

<a id="urdf"></a>
## 4. URDF: inspect and validate

URDF provides chains, joint ordering, limits, velocity bounds, and forward kinematics. It cannot automatically identify source fields, command/feedback semantics, clocks, cameras, tasks, or all units.

```bash
edc robot urdf --urdf /path/to/robot.urdf
edc robot urdf --urdf /path/to/robot.urdf --base base_link --tip tool_link
```

Declare the chain under its arm, with joints in **recorded column order**:

```yaml
urdf:
  file: robot/arm.urdf
  joints: [joint1, joint2, joint3, joint4, joint5, joint6]
  base_link: base_link
  tip_link: tool_link
  # tip_offset: [0, 0, 0.1]  # Recorded tool point in tip_link coordinates, m
```

The file path is relative to the robot YAML. Joint directions, zero positions, and chain must match the recording. Only expanded URDF is accepted. `eef_position: fk` computes positions from configured joints and the chain; it does not independently validate them.

```bash
edc robot urdf --robot /path/to/robot.yaml \
  --data /path/to/episode_1 /path/to/episode_2
```

When measured end-effector data exists, the first episode estimates frame relations and the second checks them. Without measured end-effector data or two usable episodes, validation is limited to available joint checks. Limited motion, errors absorbed into frame relations, and small deviations can remain undetected. Home, tolerances, tool points, and calibration scope need explicit declarations.

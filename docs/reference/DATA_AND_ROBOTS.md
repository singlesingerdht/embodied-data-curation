# Formats and Configuration Reference

[Home](../../README.md) · [简体中文](DATA_AND_ROBOTS.zh-CN.md) · [Data and Robot Configuration](../how-to/DATA_AND_ROBOT.md)

This page is for **lookup**: which data formats are supported, which robot configurations ship with the repository, which robot-YAML fields exist, and how units and time are declared.
Step-by-step instructions are in [Data and Robot Configuration](../how-to/DATA_AND_ROBOT.md); format dependencies are installed as described in the [quick start](../tutorial/QUICKSTART.md).

Readers extract numbers, images, and timestamps from files. Robot configuration describes which part each recording belongs to, what it represents, and which units it uses. Choose a matching format and configuration, then check which algorithms can run on the data.

<a id="formats"></a>
## Data formats

| Format | What can be read | Requirements |
|---|---|---|
| LeRobot v2.1 / v3.0 | Numeric columns, supported image columns, and associated videos | Keep metadata and indices describing each operation, including its video range; configure the meaning of each column. The official reader needs Python ≥3.12 and `.[lerobot]`; an alternative reader is provided by `.[formats]` |
| HDF5 | Numeric arrays and images | Specify the group for each operation, such as `data/demo_0`; one file may contain several operations. Configure array names, component order, and time information |
| Parquet | Numeric columns and supported image columns | Episodes are grouped by `episode_index` (or the profile’s `episode_column`); without it, the file is read as one episode |
| ROS1 / ROS2 bags | Joints, poses, IMU, images, and timestamps in supported messages | Requires `rosbags` and readable message definitions. Custom messages also need support for extracting the required fields |
| MCAP | Numbers, images, and timestamps in supported ROS1, ROS2 CDR, or Protobuf messages | Requires `mcap` and the corresponding decoding libraries. Message definitions must be available in the file; Protobuf needs an embedded descriptor. Unreadable content limits the corresponding checks |
| JSON with video directories | Per-record numeric values and camera videos, such as X1 Pro robot-bridge exports | JSON structure, data names, and video layout must match the reader; correspondence between numeric records and video frames must be specified |
| Standalone MP4 | Video frames and playback timestamps | Supports image/video checks. A video alone provides no joint positions, and playback time cannot establish acquisition synchronization |

See [`src/edc/data/readers/`](../../src/edc/data/readers/) for implementations. Images and videos both support visual checks; checks for video-file damage require a video file.

<a id="configs"></a>
## Existing robot configurations

These configurations describe specific data layouts. Match both the file format and data names; different recording setups for the same robot may require different configurations.

| Configuration | Matching data | Configured content and requirements |
|---|---|---|
| [X1 Pro · MCAP](../../configs/robots/x1pro_mcap.yaml) | The MCAP recording used in quick start | Both arms' joints and end effectors, four color cameras, head depth, and other recordings. Joint position/velocity and end-effector XYZ units are declared; clock relationships, depth scale, and some numeric limits remain unconfirmed |
| [X1 Pro · JSON + video](../../configs/robots/x1pro.yaml) | JSON with three MP4 files exported by robot-bridge | Both arms, master/follower recordings, and head/wrist cameras; numeric names include `follow_*` and `master_*`. Some units, including gripper units, are unknown. This configuration cannot directly replace the MCAP configuration |
| [Leju KUAVO 4pro](../../configs/robots/leju_kuavo.yaml) | ROS1 bags from the dexterous-hand version | Both arms' joints, hands, IMU, and cameras; end-effector positions are calculated from joints and URDF. Depth scale is unconfirmed, so metric depth checks are unavailable |
| [Luoshi dual arm](../../configs/robots/luoshi.yaml) | The LeRobot v3.0 layout described in the configuration | A 14-element joint array split into seven left and seven right joints, plus three color cameras. No measured end-effector positions; commands cannot substitute for feedback. Joint limits and speed bounds are absent |
| [pepsi_sort single arm](../../configs/robots/pepsi_sort.yaml) | The LeRobot v3.0 layout described in the configuration | End-effector pose, gripper, and wrist camera. No joint angles, and gripper units are unknown. Frame-generated timestamps cannot support acquisition synchronization checks |
| [Franka Panda · simulation](../../configs/robots/franka_panda.yaml) | robomimic/robosuite HDF5 data | Single-arm joints, end effector, and gripper; `data/demo_*` groups hold separate operations. This is simulation data, and its 20 Hz frame timeline is not recorded acquisition time |

These files describe how to read and check the corresponding data. They do not establish support for every recording from the same robot model. Algorithm availability still depends on the actual file contents and settings.

## What to verify before use

- **How is the data stored?** A bag/MCAP message is a timestamped record. Its message type describes the fields and their storage, for example `position` and `velocity` in a joint message. A custom structure needs a reader that can decode it and extract the numbers; recognizing the file extension is not enough.
- **What do the numbers represent?** Configuration must specify whether `position` is a joint angle or an end-effector position, whether it uses degrees, radians, or metres, whether `effort` is current or torque, and whether records are commands or measured feedback. These meanings cannot be inferred from field names alone.
- **Can the images and times support the selected check?** Images must decode into frames. Depth also needs a conversion to metres and rules for invalid measurements. Timestamps need a known meaning and unit. Comparing different devices also requires a confirmed relationship between their clocks; see [units and time](#units-time).

<a id="robot-fields"></a>
## Robot YAML fields

Start from [single_arm.yaml](../../configs/templates/single_arm.yaml), [dual_arm.yaml](../../configs/templates/dual_arm.yaml), or [dual_arm_hand.yaml](../../configs/templates/dual_arm_hand.yaml).

| Section | Responsibility |
|---|---|
| `robot` | Name, confirmation owner, signature; `name` begins with an ASCII letter and `confirmed_by` is required; `signature` is written by `edc batch` when it asks for a signature after the yaml changes, do not edit it by hand |
| `data` | Explicit time column, absent acquisition-time explanation, minimum duration, HDF5 episode group |
| `arms` | Actual arms, joint/end-effector feedback, commands, grippers/hands, Home, and optional URDF |
| `cameras` | Source, camera role, RGB/depth, and wrist ownership; `info` names the matching camera-info topic paired by the structure check |
| `imu` / `other` | IMU components and other declared numeric fields |
| `checks` | Algorithm parameters, channel selection, and scope for this run; `skip: [algorithm_id]` skips a check |

`source` identifies the recorded field; `field` selects a message member such as `position`; `index` selects components. `0-5` is inclusive; lists such as `[0, 2, 4]` are also supported. Joint declarations require explicit component counts. Derived logical names such as `joint/arm/main/feedback` bind algorithms to the declared signal; names cannot turn commands into feedback.

<a id="scope"></a>
### Declared robot versus run scope

**Declare the robot separately from the run scope:**

| Declared arms | Algorithm parameter | Scope |
|---|---|---|
| `main` | `arms: all` | The single arm |
| `left`, `right` | `arms: all` | Both arms, checked separately |
| `left`, `right` | `arms: [left]` | Left arm and explicitly retained shared inputs |
| `left`, `right` | `arms: [right]` | Right arm and explicitly retained shared inputs |

For combined arrays, declare distinct indices; for separate arrays, use distinct sources. Fields covered by component views are not checked again as complete raw arrays. Missing arms or unknown ownership cannot silently become full single-arm coverage. [Scope details, Chinese](EVIDENCE_AND_SCOPE.en.md)

Reuse robot configuration across task datasets while keeping tasks and episode metadata with each dataset. Recheck source mapping, units, calibration, and clock applicability for each capture batch.

<a id="units-time"></a>
## Units and time

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

## Related pages

- [Data and Robot Configuration](../how-to/DATA_AND_ROBOT.md): inspect fields, write the configuration, validate with URDF.
- [Run Algorithms](../how-to/RUN_AND_SELECT.md): check what the current configuration can run.
- [Scope and evidence reference](EVIDENCE_AND_SCOPE.en.md): arm scope, record-header evidence, and joint freeze evidence.

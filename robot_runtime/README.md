# Local Luna runtime

This directory is the machine-local home for the RPent clothes-grasp runtime.
Compiled ROS and robot-controller artifacts are intentionally not committed.

Expected layout:

```text
robot_runtime/
├── luna_ros_overlay/
│   ├── build/
│   └── install/setup.bash
├── ros_deps/
│   └── root/opt/ros/humble/    # project-local CycloneDDS packages
└── Apex_Deploy/                 # optional when generated interfaces need it
```

The Luna overlay must provide these ROS 2 Python interfaces:

- `marvin_msgs.msg.JointcmdArm`
- `marvin_msgs.msg.JointcmdBody`
- `marvin_msgs.msg.JointcmdHead`
- `marvin_msgs.msg.Jointfeedback`
- `marvin_msgs.srv.Int`

Do not substitute the installed `robotnode_marvin_msgs` package: it has a
different package name and does not provide the three Luna command messages.

The checked-in environment loader also supports locally extracted ROS Debian
packages under `ros_deps/root`, so CycloneDDS does not require a system-wide
sudo installation.

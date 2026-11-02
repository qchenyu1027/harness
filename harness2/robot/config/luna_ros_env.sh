#!/usr/bin/env bash
# Project-local ROS environment for the RPent Luna clothes grasp.
# This file is sourced by the trusted Harness backend before any arm motion.

unset PYTHONHOME PYTHONPATH VIRTUAL_ENV
unset CONDA_PREFIX CONDA_DEFAULT_ENV CONDA_PYTHON_EXE _CE_CONDA _CE_M
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

HARNESS_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
LOCAL_RUNTIME="${HARNESS_ROBOT_RUNTIME:-${HARNESS_ROOT}/robot_runtime}"
ROS_SETUP="/opt/ros/humble/setup.bash"
LUNA_OVERLAY="${LUNA_ROS_OVERLAY:-${LOCAL_RUNTIME}/luna_ros_overlay/install/setup.bash}"

if [[ ! -f "${ROS_SETUP}" ]]; then
  echo "ROS Humble setup is missing: ${ROS_SETUP}" >&2
  return 1
fi
source "${ROS_SETUP}"

if [[ ! -f "${LUNA_OVERLAY}" ]]; then
  echo "Local Luna ROS overlay is missing: ${LUNA_OVERLAY}" >&2
  echo "It must provide marvin_msgs JointcmdArm/JointcmdBody/JointcmdHead/Jointfeedback and Int.srv." >&2
  return 1
fi
COLCON_CURRENT_PREFIX="$(dirname -- "${LUNA_OVERLAY}")"
source "${LUNA_OVERLAY}"
unset COLCON_CURRENT_PREFIX

LOCAL_ROS_DEPS="${LOCAL_RUNTIME}/ros_deps/root/opt/ros/humble"
if [[ -d "${LOCAL_ROS_DEPS}" ]]; then
  export AMENT_PREFIX_PATH="${LOCAL_ROS_DEPS}${AMENT_PREFIX_PATH:+:${AMENT_PREFIX_PATH}}"
  export CMAKE_PREFIX_PATH="${LOCAL_ROS_DEPS}${CMAKE_PREFIX_PATH:+:${CMAKE_PREFIX_PATH}}"
  export LD_LIBRARY_PATH="${LOCAL_ROS_DEPS}/lib:${LOCAL_ROS_DEPS}/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
fi

export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}"
export ROS_LOCALHOST_ONLY="${ROS_LOCALHOST_ONLY:-0}"
export RMW_IMPLEMENTATION="${RMW_IMPLEMENTATION:-rmw_cyclonedds_cpp}"
export CYCLONEDDS_URI="${CYCLONEDDS_URI:-file:///home/tjzn-zwt/Aqqqcy/github/RPent/robots/luna/config/cyclonedds_remote.xml}"
export LUNA_APEX_WORKSPACE="${LUNA_APEX_WORKSPACE:-${LOCAL_RUNTIME}/Apex_Deploy}"

if [[ -d "${LUNA_APEX_WORKSPACE}/build" ]]; then
  while IFS= read -r generated_python_dir; do
    export PYTHONPATH="${generated_python_dir}${PYTHONPATH:+:${PYTHONPATH}}"
  done < <(find "${LUNA_APEX_WORKSPACE}/build" -type d -name rosidl_generator_py -print)
fi

if [[ -d "${LUNA_APEX_WORKSPACE}/install" ]]; then
  while IFS= read -r package_lib_dir; do
    export LD_LIBRARY_PATH="${package_lib_dir}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
  done < <(find "${LUNA_APEX_WORKSPACE}/install" -type d -name lib -print)
fi

export NO_PROXY="${NO_PROXY:+${NO_PROXY},}6.6.7.100"
export no_proxy="${no_proxy:+${no_proxy},}6.6.7.100"

if ! /usr/bin/python3 -c \
  'from marvin_msgs.msg import JointcmdArm, JointcmdBody, JointcmdHead, Jointfeedback; from marvin_msgs.srv import Int' \
  >/dev/null 2>&1; then
  echo "Local Luna overlay is incompatible: required marvin_msgs interfaces cannot be imported." >&2
  return 1
fi

unset HARNESS_ROOT LOCAL_RUNTIME ROS_SETUP LUNA_OVERLAY LOCAL_ROS_DEPS

首次调用 plan_robot_task，mode 为 execute、answer 或 clarify。execute 的 steps 为完整动作列表，每项为 tool 和 arguments；咨询与澄清时 steps 为空。
移动步骤例：{"tool":"move_robot","arguments":{"pose_id":2}}。
抓衣步骤例：{"tool":"grasp_clothes","arguments":{}}。
后续每轮调用一个动作工具。只依据真实工具回传推进；ok 不为 true，或存在 cancelled/result_unknown 时结束。
模型无法通过修改提示词增加底层动作能力。

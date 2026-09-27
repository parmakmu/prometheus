---
name: workflow-batcher
description: Command runner (no model) that splits a map stage's items into batch files, merges batch outputs, and runs a stage's program and its gate. Used only by compiled workflow scripts.
runner:
  type: external-cli
  command: python3
  args: ["../../../.pi/skills/workflow/scripts/batch_tool.py"]  # cwd is runs/<id>/work
  promptDelivery: stdin
inheritProjectContext: false
inheritGlobalContext: false
inheritSkills: false
---

Batch tool request follows on the last BATCH_TOOL_REQUEST line.

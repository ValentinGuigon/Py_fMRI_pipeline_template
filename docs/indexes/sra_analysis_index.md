# Analysis Index: sra

Generated: `2026-04-01T17:47:13Z`

## Summary

- Models indexed: 2
- PDF reports found: 0
- JSON index written separately: `sra_analysis_index.json`

## Analyses

| Model | Kernel (mm) | GLM | Contrasts | Reports |
| --- | --- | --- | --- | --- |
| `sra_motor_lateralization` | `4` | Run/runLevel: glm [1, trial_type.button_press, trial_type.button_press_left, trial_type.button_press_right, trial_type.self_choice, trial_type.self_choice_validation, trial_type.self_iti, framewise_displacement, trans_x, trans_y, trans_z, rot_x, rot_y, rot_z, w_comp_cor_00, w_comp_cor_01, w_comp_cor_02, w_comp_cor_03, w_comp_cor_04, c_comp_cor_00, c_comp_cor_01, c_comp_cor_02, c_comp_cor_03, c_comp_cor_04]<br>Dataset/datasetLevel: glm [1] | runLevel/RightVsLeft (t): -1.0:button_press_left, 1.0:button_press_right<br>runLevel/LeftVsRight (t): 1.0:button_press_left, -1.0:button_press_right | - |
| `sra_visual_vs_baseline` | `4` | Run/runLevel: glm [1, trial_type.self_choice, trial_type.self_iti, framewise_displacement, trans_x, trans_y, trans_z, rot_x, rot_y, rot_z, w_comp_cor_00, w_comp_cor_01, w_comp_cor_02, w_comp_cor_03, w_comp_cor_04, c_comp_cor_00, c_comp_cor_01, c_comp_cor_02, c_comp_cor_03, c_comp_cor_04]<br>Dataset/datasetLevel: glm [1] | runLevel/selfDecisionVsITI (t): 1.0:self_choice, -1.0:self_iti | - |

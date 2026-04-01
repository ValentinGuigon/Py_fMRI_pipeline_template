# Analysis Index: tmth

Generated: `2026-04-01T17:46:58Z`

## Summary

- Models indexed: 4
- PDF reports found: 3
- JSON index written separately: `tmth_analysis_index.json`

## Analyses

| Model | Kernel (mm) | GLM | Contrasts | Reports |
| --- | --- | --- | --- | --- |
| `tmth_motor_bilateral` | `4` | Run/runLevel: glm [1, trial_type.button_press, trial_type.button_press_left, trial_type.button_press_right, trial_type.choice, trial_type.wait, trial_type.feedback, trial_type.fixation, framewise_displacement, trans_x, trans_y, trans_z, rot_x, rot_y, rot_z, w_comp_cor_00, w_comp_cor_01, w_comp_cor_02, w_comp_cor_03, w_comp_cor_04, c_comp_cor_00, c_comp_cor_01, c_comp_cor_02, c_comp_cor_03, c_comp_cor_04]<br>Subject/subjectLevel: meta [1]<br>Dataset/datasetLevel: glm [1] | runLevel/motorVsBaseline (t): 1.0:button_press, -0.25:choice, -0.25:wait, -0.25:feedback, -0.25:fixation | reports/tmth/tmth_motor_bilateral_s4__p-unc_p0.01_2s.pdf |
| `tmth_motor_lateralization` | `4` | Run/runLevel: glm [1, trial_type.button_press_left, trial_type.button_press_right, trial_type.choice, trial_type.wait, trial_type.feedback, trial_type.fixation, framewise_displacement, trans_x, trans_y, trans_z, rot_x, rot_y, rot_z, w_comp_cor_00, w_comp_cor_01, w_comp_cor_02, w_comp_cor_03, w_comp_cor_04, c_comp_cor_00, c_comp_cor_01, c_comp_cor_02, c_comp_cor_03, c_comp_cor_04]<br>Subject/subjectLevel: meta [1]<br>Dataset/datasetLevel: glm [1] | runLevel/RightVsLeft (t): -1.0:button_press_left, 1.0:button_press_right<br>runLevel/LeftVsRight (t): 1.0:button_press_left, -1.0:button_press_right | reports/tmth/tmth_motor_lateralization_s4__p-unc_p0.01_2s.pdf |
| `tmth_motor_vs_fixation` | `4` | Run/runLevel: glm [1, trial_type.button_press, trial_type.choice, trial_type.wait, trial_type.feedback, trial_type.fixation, framewise_displacement, trans_x, trans_y, trans_z, rot_x, rot_y, rot_z, w_comp_cor_00, w_comp_cor_01, w_comp_cor_02, w_comp_cor_03, w_comp_cor_04, c_comp_cor_00, c_comp_cor_01, c_comp_cor_02, c_comp_cor_03, c_comp_cor_04]<br>Subject/subjectLevel: meta [1]<br>Dataset/datasetLevel: glm [1] | runLevel/motorVsBaseline (t): 1.0:button_press, -1.0:fixation | - |
| `tmth_visual_vs_baseline` | `4` | Run/runLevel: glm [1, trial_type.choice, trial_type.wait, trial_type.feedback, trial_type.fixation, framewise_displacement, trans_x, trans_y, trans_z, rot_x, rot_y, rot_z, w_comp_cor_00, w_comp_cor_01, w_comp_cor_02, w_comp_cor_03, w_comp_cor_04, c_comp_cor_00, c_comp_cor_01, c_comp_cor_02, c_comp_cor_03, c_comp_cor_04]<br>Subject/subjectLevel: meta [1]<br>Dataset/datasetLevel: glm [1] | runLevel/visualVsBaseline (t): 0.3333333333333333:choice, 0.3333333333333333:wait, 0.3333333333333333:feedback, -1.0:fixation | reports/tmth/tmth_visual_vs_baseline_s4__p-unc_p0.01_2s.pdf |

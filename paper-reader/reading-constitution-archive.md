## Archived Rules

### R-INTRO-03

**Rule:** Record all forward references to methods, theorems, or datasets mentioned in the introduction; these are navigation anchors for later sections.

**Source:** Initial constitution, v1

**Confidence:** medium

### R-MODEL-03

**Rule:** Map each model component to its role in the paper's main result; components with no downstream use in theorems or experiments should be flagged as background rather than core.

**Source:** Initial constitution, v1

**Confidence:** medium

### R-METHOD-03

**Rule:** Note any implementation details that deviate from the theoretical description (e.g., numerical stabilizers, tie-breaking rules, stopping criteria), as these affect reproducibility.

**Source:** Initial constitution, v1

**Confidence:** medium

### R-SIM-03

**Rule:** Note whether the simulation DGP satisfies all model assumptions stated in R-MODEL. Simulations that violate their own model assumptions should be flagged as a design concern.

**Source:** Initial constitution, v1

**Confidence:** medium

### R-REAL-03

**Rule:** Check for preprocessing choices (outlier removal, normalization, missing-data handling) that are not documented in the main text. Undocumented preprocessing is a reproducibility flag.

**Source:** Initial constitution, v1

**Confidence:** medium

### R-DISC-03

**Rule:** Note future-work directions that are directly supported by the paper's results versus those that are speculative extensions. Speculative directions should not be recorded as established findings.

**Source:** Initial constitution, v1

**Confidence:** medium

### R-SYNTH-03

**Rule:** List open questions that remain after reading: unresolved assumptions, missing proofs, or experimental gaps. These populate the paper's open-questions field in the claims sidecar.

**Source:** Initial constitution, v1

**Confidence:** medium

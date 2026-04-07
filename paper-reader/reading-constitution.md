---
version: 1
updated: 2026-03-22
changelog:
  - version: 1
    date: 2026-03-21
    summary: Initial Layer A reading constitution. Covers all 8 section types with 2+ rules each.
---

# Reading Constitution

Layer A shared ruleset for the paper-reader comprehension pipeline. Each rule encodes a general reading strategy applicable across papers and domains. Rules are organized by the section type they govern.

---

## Section: Introduction (R-INTRO)

### R-INTRO-01

**Rule:** Identify the paper's primary research question or contribution claim in the first pass. Do not move to later sections until a candidate claim is recorded, even if provisional.

**Source:** Initial constitution, v1

**Confidence:** high

---

### R-INTRO-02

**Rule:** Note the gap or limitation in prior work that the paper claims to address. If no explicit gap statement is found, mark the gap as implicit and infer it from the motivation paragraph.

**Source:** Initial constitution, v1

**Confidence:** high

---

### R-INTRO-03

**Rule:** Record all forward references to methods, theorems, or datasets mentioned in the introduction; these are navigation anchors for later sections.

**Source:** Initial constitution, v1

**Confidence:** medium

---

## Section: Model (R-MODEL)

### R-MODEL-01

**Rule:** Extract the formal definition of every named model object (process, distribution, function class) and store it verbatim before interpreting it. Paraphrase only after the literal form is preserved.

**Source:** Initial constitution, v1

**Confidence:** high

---

### R-MODEL-02

**Rule:** Flag every model assumption explicitly. Distinguish between identifiability assumptions, regularity conditions, and computational approximations.

**Source:** Initial constitution, v1

**Confidence:** high

---

### R-MODEL-03

**Rule:** Map each model component to its role in the paper's main result; components with no downstream use in theorems or experiments should be flagged as background rather than core.

**Source:** Initial constitution, v1

**Confidence:** medium

---

## Section: Method (R-METHOD)

### R-METHOD-01

**Rule:** Identify whether the method is a closed-form procedure, an iterative algorithm, or a heuristic. Record computational complexity claims if stated.

**Source:** Initial constitution, v1

**Confidence:** high

---

### R-METHOD-02

**Rule:** Check that each step in the method maps to a stated justification (theoretical guarantee, empirical observation, or design choice). Unjustified steps should be flagged for follow-up.

**Source:** Initial constitution, v1

**Confidence:** high

---

### R-METHOD-03

**Rule:** Note any implementation details that deviate from the theoretical description (e.g., numerical stabilizers, tie-breaking rules, stopping criteria), as these affect reproducibility.

**Source:** Initial constitution, v1

**Confidence:** medium

---

## Section: Theory (R-THEORY)

### R-THEORY-01

**Rule:** Record the exact statement of every theorem, lemma, and corollary before reading its proof. Do not let proof details overwrite the statement in working memory.

**Source:** Initial constitution, v1

**Confidence:** high

---

### R-THEORY-02

**Rule:** Identify the proof strategy at the top level (direct, contradiction, induction, coupling, martingale argument, etc.) before descending into case analysis or algebraic manipulation.

**Source:** Initial constitution, v1

**Confidence:** high

---

### R-THEORY-03

**Rule:** Cross-check each theorem's assumptions against the model assumptions recorded in R-MODEL. Mismatches between assumed and required conditions are high-priority findings.

**Source:** Initial constitution, v1

**Confidence:** high

---

## Section: Simulation (R-SIM)

### R-SIM-01

**Rule:** Record the data-generating process (DGP) for each simulation: distribution family, parameter values, sample size, and number of replications. These are necessary for reproducibility assessment.

**Source:** Initial constitution, v1

**Confidence:** high

---

### R-SIM-02

**Rule:** Check whether simulation results are presented with uncertainty estimates (standard errors, confidence intervals, or quantiles across replications). Results without uncertainty are flagged as incomplete evidence.

**Source:** Initial constitution, v1

**Confidence:** high

---

### R-SIM-03

**Rule:** Note whether the simulation DGP satisfies all model assumptions stated in R-MODEL. Simulations that violate their own model assumptions should be flagged as a design concern.

**Source:** Initial constitution, v1

**Confidence:** medium

---

## Section: Real Data (R-REAL)

### R-REAL-01

**Rule:** Record the dataset name, provenance, and size before evaluating empirical results. Results cannot be assessed without knowing what data was used.

**Source:** Initial constitution, v1

**Confidence:** high

---

### R-REAL-02

**Rule:** Identify whether the real-data analysis is confirmatory (pre-specified hypothesis) or exploratory. Flag exploratory analyses that are presented as confirmatory.

**Source:** Initial constitution, v1

**Confidence:** high

---

### R-REAL-03

**Rule:** Check for preprocessing choices (outlier removal, normalization, missing-data handling) that are not documented in the main text. Undocumented preprocessing is a reproducibility flag.

**Source:** Initial constitution, v1

**Confidence:** medium

---

## Section: Discussion (R-DISC)

### R-DISC-01

**Rule:** Compare the paper's self-assessed contributions against the evidence collected in earlier sections. Overclaims relative to the evidence are a high-priority finding.

**Source:** Initial constitution, v1

**Confidence:** high

---

### R-DISC-02

**Rule:** Record every limitation the authors acknowledge. Add any additional limitations identified during reading that are not mentioned by the authors.

**Source:** Initial constitution, v1

**Confidence:** high

---

### R-DISC-03

**Rule:** Note future-work directions that are directly supported by the paper's results versus those that are speculative extensions. Speculative directions should not be recorded as established findings.

**Source:** Initial constitution, v1

**Confidence:** medium

---

## Section: Summary Synthesis R-SYNTH

### R-SYNTH-01

**Rule:** Produce a single-sentence contribution summary that references the method, the theoretical or empirical result, and the setting. This sentence is the canonical claim for the paper record.

**Source:** Initial constitution, v1

**Confidence:** high

---

### R-SYNTH-02

**Rule:** Rate overall evidence strength on a three-point scale: strong (theorem + simulation + real data), moderate (theorem + one of simulation/real data), or weak (no theorem and limited empirics). Record the rating and its justification.

**Source:** Initial constitution, v1

**Confidence:** high

---

### R-SYNTH-03

**Rule:** List open questions that remain after reading: unresolved assumptions, missing proofs, or experimental gaps. These populate the paper's open-questions field in the claims sidecar.

**Source:** Initial constitution, v1

**Confidence:** medium


# Proof Pattern Library

Layer A shared library of general statistical proof patterns. Entries describe recurring argument structures that appear across mathematical statistics and probability papers. These patterns are domain-agnostic; domain-specific variants belong in Layer B vault meta-notes.

---

## How to Use

When reading a theorem proof in R-THEORY, identify which pattern (or combination of patterns) the proof uses. Record the matched pattern ID in the claims sidecar under `proof_patterns`. If no pattern fits, add a proposal to `reading-constitution-proposals.md`.

---

## Patterns

### R-PROOF-01: Continuous Mapping / Delta Method

**Pattern:** The quantity of interest is a smooth function of a simpler estimator whose limit is known. Apply the continuous mapping theorem or delta method to transfer convergence.

**Trigger signals:** "by the continuous mapping theorem", "by the delta method", Taylor expansion of a statistic around its probability limit, functions of asymptotically normal estimators.

**Key requirement:** The function must be differentiable (or at least continuous) at the limit point. Verify this condition is checked in the proof.

**Confidence:** high

**Source:** Initial constitution, v1

---

### R-PROOF-02: Martingale Central Limit Theorem (CLT)

**Pattern:** A sum of dependent terms is shown to be a martingale difference sequence (or close to one). The martingale CLT then yields asymptotic normality without requiring i.i.d. observations.

**Trigger signals:** "predictable quadratic variation", "conditional variance", "martingale difference array", Doob's optional stopping theorem, Rebolledo's theorem.

**Key requirement:** Verify the Lindeberg-type condition (conditional Lyapunov or Lindeberg condition) is established for the MDS array.

**Confidence:** high

**Source:** Initial constitution, v1

---

### R-PROOF-03: Coupling / Stochastic Domination

**Pattern:** Two processes or distributions are coupled on a common probability space so that one stochastically dominates the other. Bounds on the simpler process transfer to the target.

**Trigger signals:** "construct a coupling", "stochastic domination", "we may assume without loss of generality that", monotone couplings, Strassen's theorem.

**Key requirement:** Check that the coupling is explicitly constructed and that the domination inequality holds pathwise (not just in distribution).

**Confidence:** high

**Source:** Initial constitution, v1

---

### R-PROOF-04: Uniform Law of Large Numbers (ULLN) / Glivenko-Cantelli

**Pattern:** Convergence of an empirical objective function to its population counterpart is established uniformly over a parameter space, enabling argmax / argmin consistency.

**Trigger signals:** "uniform convergence", "bracketing entropy", "covering number", "Glivenko-Cantelli class", "pointwise LLN plus equicontinuity".

**Key requirement:** Confirm that the function class satisfies the entropy condition and that the uniform bound is tight enough to transfer to the estimator.

**Confidence:** high

**Source:** Initial constitution, v1

---

### R-PROOF-05: Bernstein / Concentration Inequality

**Pattern:** A sum of bounded or sub-exponential random variables is controlled via a Bernstein-type exponential inequality. The resulting tail bound is then taken to zero as sample size grows.

**Trigger signals:** "by Bernstein's inequality", "sub-Gaussian", "sub-exponential", "Hoeffding's inequality", "bounded differences", union bound over events.

**Key requirement:** Verify the boundedness or moment condition required by the specific inequality version used. Check that the union bound step accounts for the correct number of events.

**Confidence:** high

**Source:** Initial constitution, v1

---

### R-PROOF-06: Stein's Method / Second-Order Poincaré

**Pattern:** Normal approximation is established via Stein's equation or a Poincaré-type functional inequality, often yielding explicit Berry-Esseen-style rates without characteristic function arguments.

**Trigger signals:** "Stein equation", "Stein operator", "Poincaré inequality", "exchangeable pairs", Stein-Chen for Poisson approximation.

**Key requirement:** Check that the Stein equation is solved with an explicit regularity bound on the solution, and that the error terms in the Stein identity are controlled.

**Confidence:** medium

**Source:** Initial constitution, v1

# Domi — Graduate School Enhancement Proposals

**Prepared for:** Georgia Tech (and peer top US CS programs) application portfolio  
**Context:** Final-year CS student at Strathmore University, Nairobi  
**Codebase:** Domi rent-reconciliation platform — real users, real M-Pesa transaction data, 44-unit property  
**Goal:** Add a layer of genuine ML/AI intelligence on top of a working production system

---

## Codebase Summary (What Was Found)

### Data Available for ML

| Table | Key ML-relevant fields |
|---|---|
| `payments` | `amount`, `payment_date`, `assignment_type`, `bank_txn_id` |
| `payment_claims` | `mpesa_ref`, `claimed_amount`, `created_at`, `verified_at`, `status` |
| `payment_allocations` | Links payments → charges (FIFO); tells you which charge types got covered |
| `rent_charges` | `charge_type` (rent/service/water), `amount`, `period`, `due_date` |
| `bank_transactions` | `mpesa_ref`, `amount`, `txn_date`, `sender_name`, `txn_type` |
| `units` | `monthly_rent`, `service_charge`, `status`, `status_changed_at` |
| `tenants` | `move_in_date`, `move_out_date`, `phone` |
| `landlord_reports` | JSON blobs: monthly collection_rate, arrears, occupancy snapshots |
| `messages` | Reminder send timestamps — treatment data for causal analysis |

### What the System Already Computes (But Doesn't Learn From)

- **Collection rate** per period: verified_collected ÷ expected_income
- **Months-behind** per tenant: balance ÷ monthly_rent
- **Vacancy cost**: days_vacant × daily_rent per unit
- **Arrears concentration**: % of total arrears in top 2-3 units
- **FIFO allocation breakdown**: how much of each payment went to current vs. past-period charges

### What Is Missing (The Enhancement Gap)

- No ML inference anywhere in the codebase
- No behavioral signatures per tenant
- No anomaly detection on financial or water data
- No predictive scoring
- No time series analysis across months
- No historical balance snapshots (planned in schema but not built)

---

## Proposal 1: Tenant Payment Behavior Clustering + Default Risk Scoring

### What It Is

Build a behavioral fingerprint for each tenant from their M-Pesa payment history, cluster tenants by behavioral archetype, then train a model predicting probability of arrears next month. This is a credit risk model built entirely from mobile money data — no credit bureau, no bank account required.

### Technical Approach

**Feature engineering** from existing tables:

```python
# Computed per tenant per period
features = {
    # Timing
    'avg_days_to_pay':        mean(payment_date - period_start_date),
    'payment_timing_cv':      std(days_to_pay) / mean(days_to_pay),  # coefficient of variation

    # Amounts
    'partial_payment_ratio':  count(periods where paid < charged) / total_periods,
    'overpayment_frequency':  count(periods where paid > charged) / total_periods,
    'avg_payment_pct':        mean(payment.amount / total_charged_that_period),

    # M-Pesa claim behavior
    'claim_to_verify_lag':    mean(payment.created_at - claim.created_at),  # days
    'claim_success_rate':     count(verified claims) / count(all claims),

    # Arrears trajectory
    'arrears_slope':          linear_regression_slope(balance over last 6 periods),
    'months_behind_now':      current_balance / monthly_rent,

    # Charge coverage (from payment_allocations)
    'rent_coverage_pct':      sum(allocated to rent) / sum(rent charged),
    'water_coverage_pct':     sum(allocated to water) / sum(water charged),
}
```

**Step 1:** K-means or DBSCAN clustering on feature vectors — identify 3–5 behavioral archetypes:
- "Reliable payer" — pays on time, full amounts
- "Consistent partial payer" — always pays something, never full amount
- "Late but complete" — pays after due date but eventually settles
- "Debt spiral" — balance increasing each month
- "Sporadic" — no pattern

Evaluate with **silhouette score** and qualitative cluster descriptions.

**Step 2:** Train a gradient boosting classifier (XGBoost or LightGBM):
- Target: `in_arrears_next_month` (binary: balance > 0 at period-end)
- Evaluation: **leave-one-period-out cross-validation** (temporal split, not random — prevents data leakage)
- Output: risk score 0–1 per tenant per period

**Step 3:** Surface in caretaker dashboard — "Unit 5D: 78% likelihood of arrears next month."

**Libraries:** `scikit-learn`, `xgboost`, `pandas`, `numpy`. Zero LLM, zero API calls.

### Why It Impresses an Admissions Committee

Three things a committee cares about, all demonstrated:

1. **Feature engineering encodes domain knowledge.** The `claim_to_verify_lag` feature — how quickly a tenant submits their SMS claim after paying — is a behavioral signal that exists only in M-Pesa-based systems. It has no Western equivalent. You invented it from understanding the two-step Kenyan payment flow.

2. **Temporal evaluation is done correctly.** Leave-one-period-out cross-validation prevents data leakage in time-series classification. Many applicants would do a random train/test split on financial time series data, which is methodologically wrong. Doing it right shows graduate-level awareness.

3. **The dataset is a genuine research gap.** Kenya has no credit bureau data for informal renters. FICO scores don't exist here. You built the credit signal from M-Pesa behavioral data — a system used by 50M+ Kenyans. This is not a Kaggle dataset. This is original.

**Publication venue:** ACM DEV (Computing and Sustainable Societies), ICTD (Information & Communication Technologies and Development). The research question — "Can M-Pesa behavioral data substitute for credit bureau data in informal rental markets?" — is unanswered in the literature.

### Complexity Rating: 3/5

### Estimated Build Time: 3–4 weeks

- Week 1: Feature pipeline (SQL → pandas feature matrix, handle missing periods)
- Week 2: Clustering analysis + cluster interpretation
- Week 3: Classifier training + temporal CV evaluation
- Week 4: Dashboard integration + caretaker UI widget

### Measurable Output

- **AUC-ROC** vs. naive baseline (predict everyone in arrears = AUC 0.5)
- **Precision@K**: "Of the top 5 tenants flagged high-risk, how many actually fell into arrears?"
- **Calibration curve**: are predicted probabilities meaningful?
- **Cluster silhouette score** + qualitative behavioral archetype descriptions
- Concrete result to cite: *"Model identifies 83% of units that fell into arrears 30 days in advance, vs. 50% baseline"*

---

## Proposal 2: Water Consumption Anomaly Detection with Contextual Baselines

### What It Is

The system records variable water charges per unit per month (uploaded from meter readings into `rent_charges` with `charge_type='water'`). Build a statistical model that flags unusual water consumption for a unit — accounting for that unit's own historical baseline and the property-wide pattern — before the admin posts charges. This catches meter faults and leaks before they become billing disputes.

### Technical Approach

**Data:** `rent_charges` where `charge_type = 'water'`, grouped by `unit_id` and `period`. This is a multivariate time series: 44 units × N months.

**Per-unit statistical baseline:**

```python
# For unit U at period P:
rolling_median   = median(water_charge for U in last 3 periods)
mad              = median_absolute_deviation(last_3_periods)       # robust to outliers
z_score          = (unit_charge - rolling_median) / (mad * 1.4826) # normalized MAD

# Cross-sectional comparison:
property_median  = median(water_charge for all units in period P)
relative_dev     = unit_charge / property_median
```

**Primary detector:** Isolation Forest (`sklearn.ensemble.IsolationForest`)
- Features: `[amount, amount/rolling_baseline, amount/property_median, month_of_year, apartment_size_encoded]`
- Handles small, irregular time series better than LSTM (which needs 100s of observations)
- No labeled training data needed (unsupervised)

**Secondary detector:** CUSUM (Cumulative Sum) control chart
- Sensitive to persistent small increases rather than single spikes
- Classical statistical process control technique applied to a novel domain
- Detects "this unit's water has been creeping up for 3 months" vs. "this unit spiked once"

**Integration point:** At `/charges/water` upload — before the admin confirms charges — show: *"3 units have water charges >2σ above their 3-month baseline. Review before posting."*

**Libraries:** `scikit-learn`, `numpy`, `scipy`. Zero LLM.

### Why It Impresses an Admissions Committee

- Combining Isolation Forest with CUSUM is a methodological choice that shows breadth: you know both the ML toolkit and classical statistical process control.
- Using MAD (median absolute deviation) instead of mean/std shows you understand robustness to outliers — if one month's charge was 5× normal (meter fault), the mean-based baseline would be corrupted. MAD is not.
- **Kenyan context matters:** Nairobi water supply is intermittent. A spike might mean the water came back after a cut and the tank overflowed — not a leak. A sustained increase is more diagnostic. CUSUM catches the sustained case; Isolation Forest catches the spike. The combination is justified by domain knowledge, not just thrown together.

### Complexity Rating: 2/5

### Estimated Build Time: 2 weeks

- Week 1: Statistical pipeline (rolling baselines, MAD, CUSUM)
- Week 2: Isolation Forest integration + UI alert widget at water upload

### Measurable Output

- Anomaly rate on historical data (how many periods had flagged units?)
- False positive rate (manually review flagged charges — were they real anomalies?)
- **Estimated KES value saved**: "Flagged 3 charges totaling KES 12,000 that were water meter faults"
- CUSUM vs. Isolation Forest comparison — which catches different anomaly types?

---

## Proposal 3: Collection Rate Forecasting via Early-Month Payment Signals

### What It Is

By day 10 of the month, some tenants have already paid. Use early payment signals — who paid, how much, which units — to forecast end-of-month collection rate. The property manager gets: *"Based on what's come in so far, you're on track for 71% collection by month-end, compared to your 3-month average of 78%."* This tells the caretaker whether to intensify follow-ups before it's too late.

### Technical Approach

**Features at day D of month M** (computed from `payments` and `rent_charges`):

```python
features = {
    'pct_units_paid_by_day_D':      count(distinct units with payment in period M by day D) / occupied_units,
    'pct_expected_collected_by_D':  sum(payments in M by day D) / expected_income,
    'vs_same_day_last_month':       delta from same day-D metric last month,
    'high_risk_units_paid':         count(high-risk units already paid) / total_high_risk,
    'arrears_units_paid':           count(arrears units that have paid) / units_with_prior_arrears,
    'day_of_month':                 D,
    'month_of_year':                M % 12,   # seasonal signal
}
target = end_of_month_collection_rate  # from landlord_reports JSON or computed
```

**Models:**
1. Linear regression baseline (interpretable, honest about data poverty)
2. Gradient boosting (XGBoost) with leave-one-month-out CV

**Key methodological choice:** Frame as a **monotonic regression** problem. Collection rate within a month can only increase (payments arrive, none disappear). Add an isotonic regression constraint — encoding a structural domain truth as an inductive bias. This is a nontrivial modeling decision that demonstrates understanding of constrained optimization.

**Libraries:** `scikit-learn` (IsotonicRegression, GradientBoostingRegressor), `pandas`, `numpy`.

### Why It Impresses an Admissions Committee

With only 12–18 months of data at one property, data poverty is real. Acknowledging this and choosing methods valid for small samples (LOO-CV, interpretable linear baseline, monotonic constraints, confidence intervals) demonstrates methodological maturity that many graduate applicants skip past. The isotonic regression constraint specifically encodes domain knowledge as an inductive bias — that's exactly the kind of thinking research faculty want to see.

### Complexity Rating: 3/5

### Estimated Build Time: 3 weeks

### Measurable Output

- MAE and RMSE vs. naive forecast (naive = "same collection rate as last month")
- Confidence interval width at different points in the month (uncertainty shrinks as data accumulates)
- Forecast dashboard widget with confidence band
- Calibration: "When model predicts 75% ± 5%, how often does actual fall in that range?"

---

## Proposal 4: Optimal Reminder Timing from Observational Payment Data

### What It Is

The system sends reminders at fixed days before due date (10d, 5d, day-of). Build a model that learns, per tenant behavioral cluster, when a reminder is actually effective — defined as: followed by a payment within 5 days. Then personalize the reminder schedule per cluster, not per tenant (small data constraint).

### Technical Approach

This is a **causal inference** problem, not just prediction. Naive correlation (sent reminder → payment) is confounded: some tenants pay regardless of the reminder.

**Treatment-outcome table:**

```python
# Per (tenant, period, reminder):
{
    'reminder_sent':          bool,
    'days_before_due':        int,   # from reminder_settings or reminder_schedules
    'payment_within_5_days':  bool,  # outcome — from payments table
    'tenant_cluster':         int,   # from Proposal 1 clustering
    'months_in_arrears':      float, # prior balance / monthly_rent
    'has_phone':              bool,
}
```

**Method:** Doubly robust estimation (Inverse Probability Weighting + regression adjustment)
- Accounts for confounding: tenants who would have paid anyway
- Estimates **average treatment effect (ATE)** of reminder on payment probability, by cluster
- Libraries: `econml` (Microsoft's causal ML library) or `dowhy`

**Output:** Per-cluster optimal reminder window — the day-of-month range where reminders have the highest estimated causal effect.

### Why It Impresses an Admissions Committee

Most applicants would run a correlation. The committee knows the difference. Framing this as causal inference — engaging honestly with confounding — and using doubly robust estimation (a technique from econometrics grad courses) demonstrates depth. The heterogeneous treatment effects by tenant cluster (which behavioral group responds most to reminders?) is the kind of nuanced question that turns a course project into a research contribution.

### Complexity Rating: 4/5

### Estimated Build Time: 4–5 weeks

### Measurable Output

- ATE of reminder with confidence intervals (95% CI)
- Heterogeneous treatment effects by cluster
- Simulated improvement: "Optimal timing vs. fixed timing — expected collection rate delta"
- Potential conference paper (ICTD, ACM DEV)

---

## Proposal 5: M-Pesa Reference Graph for Payment Pattern Analysis

### What It Is

Build a bipartite graph where nodes are tenants and M-Pesa reference codes, and edges represent payment claims. Analyze the graph structure to detect: behavioral clusters, structural anomalies in the claim pipeline, and units with suspicious patterns (claimed but never verified, amount discrepancies, timing outliers).

### Technical Approach

```python
import networkx as nx

# Bipartite: tenants ↔ M-Pesa references
G = nx.Graph()
# Tenant nodes: {monthly_rent, months_behind, cluster, move_in_date}
# Reference nodes: {amount, verified, claim_lag_days, amount_discrepancy}
# Edges: tenant → submitted claim with this reference

# Graph features per tenant:
tenant_features = {
    'degree':                count(unique references submitted),
    'claim_success_rate':    edges leading to verified payments / total edges,
    'avg_amount_discrepancy': mean(|claimed - verified| for matched claims),
    'avg_claim_lag':         mean(days from M-Pesa timestamp to claim submission),
}
```

**Anomaly detection:** node2vec embeddings → Isolation Forest on embedding space. Structurally unusual nodes (tenants who claim differently from their neighbors) surface automatically.

**Secondary signal:** The `claim_to_verify_lag` distribution. Real M-Pesa payments appear in bank statements within 1–2 business days. Claims with references that never appear, or appear much later, cluster differently.

**Libraries:** `networkx`, `pecanpy` (node2vec), `scikit-learn`, `umap-learn` (for embedding visualization).

### Why It Impresses an Admissions Committee

Graph ML is a hot research area. Applying node2vec to a payment reconciliation graph is a concrete, novel problem formulation that connects a domain problem to a theoretical framework. The bipartite structure (tenants × payment references) is a natural representation of the reconciliation problem — showing this connection demonstrates the ability to map real systems to CS abstractions.

### Complexity Rating: 4/5

### Estimated Build Time: 4 weeks

### Measurable Output

- Graph density, degree distribution, connectivity statistics
- Embedding visualization (t-SNE or UMAP) — tenant clusters visible in embedding space
- Anomaly detection precision/recall (using synthetic injected anomalies)
- If real anomalies found: "Model flagged X cases that turned out to be duplicate claims worth KES Y"

---

## Recommendation: The Single Strongest Proposal

**Proposal 1: Tenant Payment Behavior Clustering + Default Risk Scoring**

**Why, specifically:**

1. **Complete ML workflow on display.** Feature engineering → unsupervised clustering → supervised classification → temporal evaluation → deployment. A faculty reviewer sees the full research loop.

2. **The Kenyan context is the dataset's moat.** There is no academic dataset of M-Pesa-based rent payment behavior. The `claim_to_verify_lag` feature does not exist in Western property management systems. You built the credit signal from behavioral data that no Western researcher has access to.

3. **Live system, real users, real numbers.** "This model ran on 44 real tenants' M-Pesa transaction histories and flagged 3 high-risk units 30 days before they fell into arrears" is worth 20 Kaggle competition results on the application.

4. **Publication potential.** The research question — *Can M-Pesa behavioral data substitute for credit bureau data in informal rental markets?* — is practically important and academically unanswered. ACM DEV and ICTD are appropriate venues.

5. **Methodology is defensible under faculty scrutiny.** Temporal cross-validation (not random split), gradient boosting (not black-box neural net on tiny data), calibration evaluation — these choices hold up when a faculty interviewer pushes back.

**Recommended build order:**
1. Proposal 1 (4 weeks) — the flagship piece
2. Proposal 2 (2 weeks) — fast complementary win, different method class, concrete KES savings
3. Proposal 3 (3 weeks) — if time allows, rounds out the portfolio with time series forecasting

---

*Generated via deep codebase analysis of `/home/user/rent-reconciliation` on 2026-05-03.*

//! Recall fusion, ranking and packing (brain.md, section 3).
//!
//! ```text
//! score = RRF(vector, fulltext, graph, episodic, skill)
//!       × (0.5 + 0.5 · importance) × confidence × trust_w × exp(−Δt / τ_kind)
//!
//! τ_kind : episodic 30 d · reflection 90 d · semantic ∞ (bounded by validity)
//! trust_w: human-verified 1.0 · system 0.9 · observed 0.7 · external 0.4
//! ```
//!
//! Pure functions on plain data, so the same inputs always give the same ranking. The Python
//! bindings (feature `python`) are a thin layer over them.

use std::collections::HashMap;

pub mod govern;

/// The RRF constant. 60 is the value from the original paper and the common default.
pub const RRF_K: f64 = 60.0;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Episodic,
    Semantic,
    Reflection,
}

impl Kind {
    pub fn parse(s: &str) -> Option<Self> {
        match s {
            "episodic" => Some(Self::Episodic),
            "semantic" => Some(Self::Semantic),
            "reflection" => Some(Self::Reflection),
            _ => None,
        }
    }

    /// Decay time constant in days. `None`: no time decay (validity bounds semantic facts).
    pub fn tau_days(self) -> Option<f64> {
        match self {
            Self::Episodic => Some(30.0),
            Self::Reflection => Some(90.0),
            Self::Semantic => None,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Trust {
    HumanVerified,
    System,
    Observed,
    External,
}

impl Trust {
    pub fn parse(s: &str) -> Option<Self> {
        match s {
            "human_verified" => Some(Self::HumanVerified),
            "system" => Some(Self::System),
            "observed" => Some(Self::Observed),
            "external" => Some(Self::External),
            _ => None,
        }
    }

    pub fn weight(self) -> f64 {
        match self {
            Self::HumanVerified => 1.0,
            Self::System => 0.9,
            Self::Observed => 0.7,
            Self::External => 0.4,
        }
    }
}

/// Reciprocal Rank Fusion: each retriever's ranked list of ids (best first) contributes
/// 1 / (k + rank) to every id it returned, with rank starting at 1.
pub fn fuse(rankings: &[Vec<String>], k: f64) -> HashMap<String, f64> {
    let mut fused: HashMap<String, f64> = HashMap::new();
    for ranking in rankings {
        for (i, id) in ranking.iter().enumerate() {
            *fused.entry(id.clone()).or_insert(0.0) += 1.0 / (k + (i + 1) as f64);
        }
    }
    fused
}

pub fn decay(kind: Kind, age_days: f64) -> f64 {
    match kind.tau_days() {
        Some(tau) => (-age_days.max(0.0) / tau).exp(),
        None => 1.0,
    }
}

/// The full score of one candidate.
pub fn score(
    rrf: f64,
    importance: f64,
    confidence: f64,
    trust: Trust,
    kind: Kind,
    age_days: f64,
) -> f64 {
    rrf * (0.5 + 0.5 * importance.clamp(0.0, 1.0))
        * confidence.clamp(0.0, 1.0)
        * trust.weight()
        * decay(kind, age_days)
}

#[derive(Debug, Clone)]
pub struct Candidate {
    pub id: String,
    pub kind: Kind,
    pub importance: f64,
    pub confidence: f64,
    pub trust: Trust,
    pub age_days: f64,
    pub tokens: u32,
}

/// Score candidates by their fused rank and order them, best first. Ties break by id, so
/// the order is fully deterministic.
pub fn rank(candidates: &[Candidate], fused: &HashMap<String, f64>) -> Vec<(String, f64)> {
    let mut scored: Vec<(String, f64)> = candidates
        .iter()
        .map(|c| {
            let rrf = fused.get(&c.id).copied().unwrap_or(0.0);
            let s = score(rrf, c.importance, c.confidence, c.trust, c.kind, c.age_days);
            (c.id.clone(), s)
        })
        .collect();
    scored.sort_by(|a, b| b.1.total_cmp(&a.1).then_with(|| a.0.cmp(&b.0)));
    scored
}

/// Fill a token budget with the highest-scored items. An item that does not fit is skipped
/// and smaller ones after it still get a chance. Returns the chosen ids, best first, and the
/// tokens used.
pub fn pack(
    ranked: &[(String, f64)],
    tokens: &HashMap<String, u32>,
    budget: u32,
) -> (Vec<String>, u32) {
    let mut used = 0u32;
    let mut chosen = Vec::new();
    for (id, _) in ranked {
        let cost = tokens.get(id).copied().unwrap_or(0);
        if used + cost <= budget {
            used += cost;
            chosen.push(id.clone());
        }
    }
    (chosen, used)
}

#[cfg(feature = "python")]
mod python {
    use std::collections::HashMap;

    use pyo3::exceptions::PyValueError;
    use pyo3::prelude::*;

    use super::{Candidate, Kind, Trust};

    /// (id, kind, importance, confidence, trust, age_days, tokens)
    type PyCandidate = (String, String, f64, f64, String, f64, u32);

    fn candidate(c: PyCandidate) -> PyResult<Candidate> {
        let (id, kind, importance, confidence, trust, age_days, tokens) = c;
        Ok(Candidate {
            kind: Kind::parse(&kind)
                .ok_or_else(|| PyValueError::new_err(format!("unknown memory kind {kind:?}")))?,
            trust: Trust::parse(&trust)
                .ok_or_else(|| PyValueError::new_err(format!("unknown trust {trust:?}")))?,
            id,
            importance,
            confidence,
            age_days,
            tokens,
        })
    }

    /// RRF over retriever rankings (each a list of ids, best first).
    #[pyfunction]
    #[pyo3(signature = (rankings, k = super::RRF_K))]
    fn fuse(rankings: Vec<Vec<String>>, k: f64) -> HashMap<String, f64> {
        super::fuse(&rankings, k)
    }

    /// The brain.md score for one candidate.
    #[pyfunction]
    fn score(
        rrf: f64,
        importance: f64,
        confidence: f64,
        trust: &str,
        kind: &str,
        age_days: f64,
    ) -> PyResult<f64> {
        let trust = Trust::parse(trust)
            .ok_or_else(|| PyValueError::new_err(format!("unknown trust {trust:?}")))?;
        let kind = Kind::parse(kind)
            .ok_or_else(|| PyValueError::new_err(format!("unknown memory kind {kind:?}")))?;
        Ok(super::score(
            rrf, importance, confidence, trust, kind, age_days,
        ))
    }

    /// Fuse, score, order and pack in one call. Returns [(id, score)] of the chosen items,
    /// best first, and the tokens they use.
    #[pyfunction]
    #[pyo3(signature = (rankings, candidates, budget, k = super::RRF_K))]
    fn rank_and_pack(
        rankings: Vec<Vec<String>>,
        candidates: Vec<PyCandidate>,
        budget: u32,
        k: f64,
    ) -> PyResult<(Vec<(String, f64)>, u32)> {
        let cands = candidates
            .into_iter()
            .map(candidate)
            .collect::<PyResult<Vec<_>>>()?;
        let fused = super::fuse(&rankings, k);
        let ranked = super::rank(&cands, &fused);
        let tokens: HashMap<String, u32> = cands.iter().map(|c| (c.id.clone(), c.tokens)).collect();
        let (chosen, used) = super::pack(&ranked, &tokens, budget);
        let scores: HashMap<&String, f64> = ranked.iter().map(|(id, s)| (id, *s)).collect();
        let out = chosen.iter().map(|id| (id.clone(), scores[id])).collect();
        Ok((out, used))
    }

    /// The write-path governance filter: (text or None when dropped, findings, instructions).
    #[pyfunction]
    #[pyo3(signature = (text, pii_policy = "redact"))]
    fn govern(text: &str, pii_policy: &str) -> PyResult<GovernResult> {
        let policy = crate::govern::PiiPolicy::parse(pii_policy)
            .ok_or_else(|| PyValueError::new_err(format!("unknown PII policy {pii_policy:?}")))?;
        let g = crate::govern::govern(text, policy);
        Ok((g.text, g.findings.into_iter().collect(), g.instructions))
    }

    /// The trust tag for a source.
    #[pyfunction]
    fn trust_for(source: &str) -> &'static str {
        crate::govern::trust_for(source)
    }

    type GovernResult = (Option<String>, HashMap<String, u32>, bool);

    #[pymodule]
    fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
        m.add_function(wrap_pyfunction!(govern, m)?)?;
        m.add_function(wrap_pyfunction!(trust_for, m)?)?;
        m.add_function(wrap_pyfunction!(fuse, m)?)?;
        m.add_function(wrap_pyfunction!(score, m)?)?;
        m.add_function(wrap_pyfunction!(rank_and_pack, m)?)?;
        m.add("RRF_K", super::RRF_K)?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ids(v: &[&str]) -> Vec<String> {
        v.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn rrf_rewards_agreement_between_retrievers() {
        let fused = fuse(&[ids(&["a", "b", "c"]), ids(&["b", "a"])], RRF_K);
        // a: 1/61 + 1/62, b: 1/62 + 1/61, c: 1/63
        assert!((fused["a"] - fused["b"]).abs() < 1e-12);
        assert!(fused["a"] > fused["c"]);
        assert!((fused["c"] - 1.0 / 63.0).abs() < 1e-12);
    }

    #[test]
    fn score_follows_the_formula() {
        let s = score(0.5, 1.0, 0.8, Trust::System, Kind::Episodic, 30.0);
        let expected = 0.5 * 1.0 * 0.8 * 0.9 * (-1.0f64).exp();
        assert!((s - expected).abs() < 1e-12);
    }

    #[test]
    fn semantic_facts_do_not_decay_episodes_do() {
        assert_eq!(decay(Kind::Semantic, 10_000.0), 1.0);
        assert!(decay(Kind::Episodic, 30.0) < decay(Kind::Reflection, 30.0));
        assert_eq!(decay(Kind::Episodic, -5.0), 1.0, "future ages do not boost");
    }

    #[test]
    fn trust_orders_human_over_external() {
        let s = |t| score(1.0, 0.5, 1.0, t, Kind::Semantic, 0.0);
        assert!(s(Trust::HumanVerified) > s(Trust::System));
        assert!(s(Trust::Observed) > s(Trust::External));
        assert!((s(Trust::External) / s(Trust::HumanVerified) - 0.4).abs() < 1e-12);
    }

    fn cand(id: &str, tokens: u32, importance: f64) -> Candidate {
        Candidate {
            id: id.into(),
            kind: Kind::Semantic,
            importance,
            confidence: 1.0,
            trust: Trust::System,
            age_days: 0.0,
            tokens,
        }
    }

    #[test]
    fn rank_is_deterministic_and_best_first() {
        let fused = fuse(&[ids(&["x", "y", "z"])], RRF_K);
        let c = [cand("z", 1, 1.0), cand("x", 1, 0.0), cand("y", 1, 0.0)];
        let ranked = rank(&c, &fused);
        let order: Vec<&str> = ranked.iter().map(|(id, _)| id.as_str()).collect();
        // z: rank 3 but importance 1.0 → 1/63 × 1.0; x: 1/61 × 0.5; y: 1/62 × 0.5
        assert_eq!(order, ["z", "x", "y"]);
        assert_eq!(ranked, rank(&c, &fused));
    }

    #[test]
    fn pack_skips_what_does_not_fit_and_keeps_filling() {
        let ranked = vec![
            ("big".into(), 3.0),
            ("mid".into(), 2.0),
            ("small".into(), 1.0),
        ];
        let tokens = HashMap::from([("big".into(), 80), ("mid".into(), 50), ("small".into(), 15)]);
        let (chosen, used) = pack(&ranked, &tokens, 100);
        assert_eq!(chosen, ["big", "small"]);
        assert_eq!(used, 95);
    }
}

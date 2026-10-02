# Venue diversification evidence ledger

**Date:** 2026-10-03 (Asia/Shanghai)  
**Purpose:** keep independent submission baskets alive while the exact first venue and author metadata are pending.

## Decision rule

The primary basket remains IEEE. A non-IEEE journal is eligible only if the target year's Clarivate Journal Citation Reports (JCR) record or an authoritative Chinese Academy of Sciences (CAS) partition record shows SCI/SCIE indexing and Q1 for the relevant category. Scopus, SCImago, OpenAlex, publisher marketing pages, and search-result snippets are screening evidence only; none is used as final Q1 proof.

## Active baskets

| Basket | Candidate routes | Current status | Promotion gate |
|---|---|---|---|
| IEEE primary | IEEE Transactions on Instrumentation and Measurement (TIM) | Scope and official author/submission link captured; manuscript fit is conditional on measurement-science framing | Confirm article type, page/template, review model, PDF/accessibility rules, and author declarations from the current official portal; then build the TIM overlay |
| IEEE reliability fallback | IEEE Transactions on Reliability (T-Rel) | Strong RUL/reliability precedent; official pages returned 418/202 without readable rules in this audit | Recheck the official society/portal pages, add reliability/credibility and uncertainty treatment, and confirm current submission rules |
| IEEE aerospace fallback | IEEE Transactions on Aerospace and Electronic Systems (TAES) | Official scope and author-information pages captured; regular/correspondence constraints recorded | Keep the 79-second simulator/tile-period seam and tiny LEO600 partition explicit; confirm portal and template before adapting |
| SCI-Q1 fallback A | Reliability Engineering & System Safety (RESS) | Technical fit is strong; Q1 status is `Q1_VERIFICATION_REQUIRED` | Capture dated Clarivate JCR or CAS record for the target year/category |
| SCI-Q1 fallback B | Mechanical Systems and Signal Processing (MSSP) | Strong signal/RUL precedent; Q1 status is `Q1_VERIFICATION_REQUIRED` | Capture dated Clarivate JCR or CAS record and add a defensible signal-processing ablation |
| SCI-Q1 fallback C | Aerospace Science and Technology (AST) | Aerospace fit is plausible; Q1 status is `Q1_VERIFICATION_REQUIRED` | Capture dated Clarivate JCR or CAS record and resolve or bound the orbital-period seam |

The six routes share the hash-linked manuscript and F1–F10 evidence. A venue overlay may change title, abstract, related-work emphasis, layout, and declarations, but may not change the registered split, frozen candidate, final receipt, gate arithmetic, or F1 outer-fold disposition.

## New repository artifact verification

- GitHub repository: `https://github.com/1553926531-sudo/BRPHM-preview`
- Commit: `2e5c8b6ad4b1518adc6e8dbfa4d41edca07a93fc`
- Added file: `release/paper_artifacts_20261003/paper_venue_branches_20261003.tar.gz`
- Local SHA-256: `f391e83b71a32d02487800e0225215f151aea875b599b4cd4af40c1a0d6a12c6`
- GitHub Contents API SHA-1: `7ec759fc9989a23ff9e1a804933b1856b2827542`
- Rack `origin/main`: `2e5c8b6ad4b1518adc6e8dbfa4d41edca07a93fc`
- Rack `git show origin/main:path | sha256sum`: `f391e83b71a32d02487800e0225215f151aea875b599b4cd4af40c1a0d6a12c6`
- Raw download cross-check from Rack: same SHA-256.

### Latest rules-evidence upload (2026-10-03)

The official-rule capture package was uploaded after the first venue summary:

- Commit: `35c0463165dfd3964e82b3a523669b18218d8a4b`
- Added file: `release/paper_artifacts_20261003/paper_venue_rules_evidence_20261003.tar.gz`
- Local SHA-256: `7841ed7c3d5717db5d179fa6b4b5ef37ef00b73bff0115f8413bc7795279e59c`
- GitHub Contents API SHA-1: `60b0271c18f2df8c1cb45479cb1c73f343c42863`
- Rack `origin/main`: `35c0463165dfd3964e82b3a523669b18218d8a4b`
- Rack `git show origin/main:path | sha256sum`: `7841ed7c3d5717db5d179fa6b4b5ef37ef00b73bff0115f8413bc7795279e59c`
- Raw download cross-check: `7841ed7c3d5717db5d179fa6b4b5ef37ef00b73bff0115f8413bc7795279e59c`

The package contains `venue_rules_verified_20261003.md`, the source captures, exact quote extracts, build script, and machine-readable manifest. It preserves the distinction between verified IEEE submission rules and unresolved Clarivate/CAS quartile evidence.

## Q1 authority check performed on 2026-10-03

The public Clarivate JCR shell loaded with HTTP 200 at `https://jcr.clarivate.com/jcr/browse-journals`. The saved response is `clarivate_jcr_home.html` with SHA-256 `ffbafd3eb37a3014e2fbcff07015bdb3e22eb4fe13802d722b523d7c7d79c796`. Its production journal-profile API returned HTTP 401 for an ISSN lookup without an authenticated institutional session. The downloaded production JavaScript identifies the official host and journal-profile endpoint; `jcr_main.js` SHA-256 is `5d06d88f6f699bdabaaff4cf7090a1823cb6eae257b5f882273ed3569039ab717`. No quartile value was inferred from the shell or from the 401 response.

This leaves RESS, MSSP, and AST as prepared Q1 candidates, not verified Q1 journals. The next authoritative step is an authenticated institutional JCR export or a dated CAS partition record for the exact target year and category. Until that record is attached, the IEEE basket is the eligible submission route.

## Evidence confidence and remaining decisions

- **High confidence:** repository commit, local/Rack hashes, TIM/TAES official page captures, and the manuscript/evidence binding.
- **Medium confidence:** topical fit of all six routes, because it depends on editorial interpretation and the current small LEO600 evaluation.
- **Open:** exact first venue, track/section, article type, deadline, author metadata, funding/COI, and the non-IEEE Q1 authority record.

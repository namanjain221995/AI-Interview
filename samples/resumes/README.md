# Sample resumes

Realistic, entirely fictional resumes for exercising resume upload, text extraction, OCR, profile
analysis and claim verification. Regenerate them with:

```bash
cd apps/api && uv run python ../../scripts/generate_sample_resumes.py
```

Everything is invented: names, employers, schools, `example.com` e-mail addresses and 555 phone numbers.
The resumes intentionally contain **no** photo, date of birth, age, graduation years, marital status,
nationality or other protected characteristics.

## Files

| File | Format | Extraction path | Candidate |
|------|--------|-----------------|-----------|
| `jane_doe_devops.pdf` | PDF with a text layer, 1 page | text | Jane Doe — Senior DevOps Engineer |
| `jane_doe_devops.docx` | Word document | text | Jane Doe |
| `jane_doe_devops.txt` | Plain text (UTF-8) | text | Jane Doe |
| `jane_doe_devops.png` | Image resume, 1275 x 1898 px | OCR | Jane Doe |
| `jane_doe_devops.jpg` | Same image as JPEG (quality 88) | OCR | Jane Doe |
| `jane_doe_devops_scanned.pdf` | Image-only PDF, 2 pages at 150 DPI, greyscale, rotated by -0.9° / +0.7°, blur, noise, speckles and a scanner-lid shadow; **no text layer** | OCR | Jane Doe |
| `marcus_okafor_backend.pdf` | PDF with a text layer | text | Marcus Okafor — Backend Engineer (Python), mid-level |
| `sofia_martinez_frontend.docx` | Word document | text | Sofia Martinez — Junior Frontend Engineer |
| `priya_raman_ml.txt` | Plain text | text | Priya Raman — Machine Learning Engineer (LLM) |

Verified when generated: `pypdf` extracts 0 characters from both pages of the scanned PDF, and the
configured OCR endpoint (`OCR_BASE_URL`, prompt `OCR`) recovers the full text of page 1 — including every
claim below and both overlapping dates — in about 19 s.

## Jane Doe — Senior DevOps Engineer (pairs with the demo job `senior_devops_engineer`)

Stated total experience: **5.8 years** (Jan 2021 to present).

| Period | Role |
|--------|------|
| Jan 2023 – Present | Senior DevOps Engineer, Bluepeak Analytics |
| Aug 2021 – Mar 2023 | DevOps Engineer, Harborline Logistics |
| Jan 2021 – Jul 2021 | Junior Systems Engineer, Cobalt Retail Group |

Claims the interview should verify (none should be assumed true):

1. "Designed production Kubernetes platform supporting 10,000 users" (3 EKS clusters, about 120 nodes) —
   probe architecture, scaling, availability-zone failure handling and her personal design role.
2. "Led migration of 40 microservices to EKS" over nine months with no downtime — probe sequencing,
   pilot services, rollback, how many services she migrated personally.
3. "Reduced cloud costs by 40%" — probe baseline, normalisation for growth, which changes contributed.
4. "Lead a team of 3 DevOps engineers" — probe delegation, decisions, mentoring.
5. Deployment lead time from 2 days to 45 minutes (Argo CD, Argo Rollouts); SLOs for 12 services.
6. 14 incident postmortems led at Harborline; 60+ Terraform modules across 4 AWS accounts.

Deliberate inconsistency: **overlapping employment dates** — the Bluepeak role starts in Jan 2023 while the
Harborline role ends in Mar 2023 (three months of overlap). The resume does not explain it; the interviewer
should ask neutrally (for example a notice period or a part-time handover), never accusingly.

Other things worth noticing: the promotion to a senior title two years into her career; no explicit
security or networking depth beyond the skills list.

## Marcus Okafor — Backend Engineer (Python), mid-level (pairs with `backend_engineer_python`)

About 3.5 years (Apr 2023 – present). Claims: FastAPI services handling about 1.2 million requests per day;
idempotent payment-webhook processing with row locks and a transactional outbox; p95 search latency cut
from 900 ms to 180 ms with query rewrites and composite indexes; test coverage raised from 48% to 81%.

Gaps against the job: Kubernetes listed as "basic"; no explicit system-design ownership or message-queue
experience beyond Celery; no API versioning or pagination examples.

## Sofia Martinez — Junior Frontend Engineer (pairs with `frontend_engineer_react`)

About 1.5–2 years including a six-month internship (Jun 2024 – Dec 2024, then Mar 2025 – present; a
two-month gap with no explanation, which is normal and should not be probed for personal reasons).
Claims: React and TypeScript components for a tool used by 3,000 instructors; Lighthouse accessibility
score from 72 to 96; React Testing Library and MSW adoption; a personal PWA with about 1,200 monthly users.

Gaps against the job: below the 2+ years asked for; little performance (Core Web Vitals) or state
management depth; Playwright only "basic".

## Priya Raman — Machine Learning Engineer, LLM (pairs with `ml_engineer_llm`)

5+ years across analytics, data science and ML engineering (Jun 2021 – present). Claims: RAG over 2 million
support articles with hybrid retrieval and cross-encoder reranking (answer acceptance 61% to 78%); an
evaluation harness with 1,200 golden questions and a calibrated LLM judge; vLLM serving with median time to
first token cut from 1.4 s to 380 ms; fraud models with PR-AUC 0.41 to 0.58 and PSI drift monitoring.

Gaps against the job: no Kubernetes or GPU scheduling experience listed; no fine-tuning experience; MLOps
limited to MLflow and Airflow.

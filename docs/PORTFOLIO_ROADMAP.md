# Portfolio Roadmap — 11 Projects

Owner GitHub: `codeROCK-IZZY`. One repository per project, so each one can be linked from your résumé on its own.

| # | Project | Proposed repo name | Builds on |
|---|---|---|---|
| 1 | AI Predictive Maintenance Platform | `ai-predictive-maintenance` | — (sets the shared standards: FastAPI layout, auth, CI, Docker) |
| 3 | Financial Fraud Detection Engine | `fraud-detection-engine` | Reuses #1's ML evaluation + SHAP patterns; adds Kafka streaming |
| 6 | AI Research & Knowledge Engine | `knowledge-engine` | Core RAG: chunking, pgvector, hybrid search, retrieval evaluation |
| 2 | Intelligent Document AI | `document-ai` | Reuses #6's retrieval; adds OCR, classification, extraction |
| 5 | AI Developer Intelligence Platform | `dev-intelligence` | Tree-sitter parsing + #6's retrieval over code |
| 4 | Autonomous Business Operations Platform | `business-ops-agents` | LangGraph agents, approvals, audit trail |
| 10 | Multimodal AI Assistant | `multimodal-assistant` | Reuses #2's document pipeline; adds vision + speech |
| 7 | Enterprise AWS Cloud Platform | `aws-enterprise-platform` | Terraform foundation (VPC, IAM, logging) others deploy onto |
| 8 | AWS MLOps Platform | `aws-mlops-platform` | Deploys #1 or #3's model with SageMaker + MLflow |
| 9 | Distributed AI Inference Platform | `distributed-inference` | Serves #1/#3 models at scale on Kubernetes |
| 11 | Engineering Portfolio Website | `portfolio` | Showcases all of the above; can start early with a "coming soon" project list |

**Why this order:** each project reuses something proven in the one before it, so later projects go faster and the portfolio reads as a coherent body of work rather than 11 disconnected demos. The portfolio site (#11) can be started at any point — it needs your real bio, experience and certifications from you.

**Every repo gets:** `CLAUDE.md` (standards), README with architecture diagram, `docs/DESIGN.md` approved before code, CI on GitHub Actions, Docker Compose dev environment, tests, known limitations, and no secrets.

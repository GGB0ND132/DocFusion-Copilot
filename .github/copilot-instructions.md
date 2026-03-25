# Project Guidelines

## Repository Type
This workspace is currently a solution-and-competition-material repository, not an implemented application. Treat [A23_LLM_DocFusion_Solution.md](../A23_LLM_DocFusion_Solution.md) as the primary architecture source of truth unless the user adds code or overrides the direction.

## Architecture
When generating code, align with the competition-oriented two-stage design in [A23_LLM_DocFusion_Solution.md](../A23_LLM_DocFusion_Solution.md):
- Stage 1: ingest documents, parse structure, extract facts, normalize values, fuse multi-source data, and build indexes.
- Stage 2: understand the uploaded template, query the fact store, and fill the template without reprocessing the full document set.

Prefer the documented hybrid strategy of rules plus LLM understanding plus traceable structured storage. For numeric, unit, year, and entity fields, avoid free-form generation and favor validated structured outputs.

## Recommended Stack
Unless the user requests otherwise, prefer the stack proposed in [A23_LLM_DocFusion_Solution.md](../A23_LLM_DocFusion_Solution.md): FastAPI, Celery, Redis, PostgreSQL with pgvector, MinIO, python-docx, openpyxl, and an OpenAI-compatible LLM API. Keep competition deliverables practical and deployment-friendly.

## Build And Test
There is currently no application code, package manifest, or runnable test suite in this workspace. Do not invent build, run, or test commands. If code is added later, derive commands from actual project files such as package.json, pyproject.toml, requirements files, Docker Compose files, or README instructions.

## Conventions
- Optimize for the competition constraints captured in [A23_LLM_DocFusion_Solution.md](../A23_LLM_DocFusion_Solution.md): stable auto-fill accuracy, per-template latency under 90 seconds, asynchronous processing, and traceable outputs.
- Preserve provenance for extracted facts: source document, source block or cell, evidence text, confidence, and canonical-versus-candidate status.
- Prefer minimal, demonstrable implementations over heavyweight infrastructure. Do not introduce unnecessary multi-database or multi-service complexity unless the user explicitly asks for an enterprise expansion.
- When producing code or docs, keep terminology consistent with the solution document: Document, Block, Fact, TemplateTask, fact store, retrieval index, and template fill.

## Documentation
Link back to [A23_LLM_DocFusion_Solution.md](../A23_LLM_DocFusion_Solution.md) for detailed product goals, data model ideas, APIs, and demo scope instead of duplicating those sections.
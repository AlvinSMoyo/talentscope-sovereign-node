
---

# TalentScope — A Sovereign AI Node for Production Recruitment

**TalentScope** is a production-grade AI recruitment engine designed to bridge the gap between academic AI research and real-world hiring operations. Built on a **Sovereign Node** architecture, it moves beyond static analysis to deliver a self-hosted, automated, and auditable candidate processing pipeline.

The system prioritizes **operational reliability, data sovereignty, and regulatory transparency**, making it suitable for high-stakes recruitment environments.

---

## 🚀 Evolution: Beyond the Spreadsheet

Many AI recruitment prototypes assume that candidate data already exists in clean, structured formats (CSVs or tables). TalentScope deliberately rejects that assumption.

Instead, it operates on real-world recruitment inputs:

* **Unstructured PDF CVs**
* **Live email-based sourcing**

Key capabilities include:

* **Zero-Touch Ingestion:**
  An agentic **IMAP listener** monitors a dedicated recruitment inbox and automatically harvests CVs without manual intervention, optionally issuing immediate acknowledgement responses to candidates.

* **Document Intelligence:**
  Raw PDF documents are parsed, normalized, and deduplicated using **SHA-256 hashing**, ensuring integrity, provenance, and repeatability.

* **State-Managed Workflows:**
  Ingestion, evaluation, and outreach operate within a single pipeline. Workspace state is explicitly reset between recruitment campaigns to prevent residual data, context bleed, or cross-session contamination.


This transition—from spreadsheet rows to live documents—marks the shift from experimental data science to systems engineering.

---

## 🛠️ Technical Stack & Infrastructure

TalentScope is designed to be resilient, inspectable, and independent of third-party “black box” ATS platforms.

### Core Stack

* **Backend:** Python / Flask
* **Reasoning Layer:** LLM-driven decision support with structured outputs
* **Frontend:** Data-driven dashboard with real-time pulse analytics (Chart.js)

### Sovereign Deployment

* **Nginx** — Reverse proxy and SSL/TLS termination
* **Gunicorn** — WSGI server for concurrency and process management
* **Systemd** — Linux service orchestration for restart policies and fault recovery

This infrastructure ensures the system can fail, recover, and continue operating without manual supervision.

---

## ⚖️ UK Compliance & Transparency

TalentScope is designed with regulatory realism in mind, particularly for the UK and EU context.

Key principles:

* **Decision Support, Not Automation:**
  The system assists recruiters with structured analysis while preserving human oversight.

* **Rationale Logging:**
  Every candidate evaluation includes a human-readable justification, stored alongside the decision output.

* **Audit Trail:**
  All system actions—ingestion, evaluation, job description generation, and outreach—are timestamped and logged for review.

This approach aligns with **UK Equality Act 2010** expectations and GDPR accountability requirements, emphasizing explainability over opaque scoring.

---

## 🔒 Security & Privacy

* **Data Sovereignty:**
  All candidate data resides on private, self-managed infrastructure.

* **Secret Management:**
  Environment-scoped configuration (`.env`) decouples sensitive credentials from the codebase.

* **Structured AI Outputs:**
  Forced JSON schemas and constrained prompts reduce hallucination risk and improve downstream reliability.

---

## 🛡️ Intellectual Property & Licensing

This repository is licensed under **GPLv3**.

The intent is to allow others to study the **architecture and operational framework** while discouraging closed-source commercial reuse. Any derivative system built on this codebase must remain open-source.

Proprietary prompts, credentials, and deployment secrets are intentionally excluded from this repository and remain on the private production node.

---

### Summary

TalentScope represents a shift from *model experimentation* to **operational AI systems**—where ingestion, decisioning, auditability, and compliance matter as much as accuracy.

This repository documents the **engineering blueprint** behind that system.

---


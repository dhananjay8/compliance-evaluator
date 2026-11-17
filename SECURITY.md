# Security Policy

## Scope and deployment warning

The service validates file types, sizes, DOCX expansion, and filenames, and
treats document content as untrusted input to the LLM. These measures do not
replace authentication, authorization, tenant isolation, or network controls.
Those controls are not implemented. Do not expose this service directly to an
untrusted network or use it as a production multi-tenant service without adding
them.

## Reporting a vulnerability

Please do not publish exploitable details in a public issue. Use the hosting
platform's private vulnerability-reporting feature when available, or contact
the repository maintainers privately. Include the affected version, impact,
reproduction conditions, and a proposed mitigation if known. Do not attach
real customer policies or credentials.

## Sensitive data

Uploaded policy documents may contain confidential information. Keep
`UPLOAD_DIR` on an access-controlled persistent volume, restrict Elasticsearch
access, and avoid logging document contents, prompts, or secrets. The
injection detector reports fixed pattern identifiers rather than matched text.

"""Stage 6: an MCP server over the pipeline's outputs. Runs locally over stdio; nothing
is deployed. Every tool reads the same BigQuery views dbt builds, so a chat client sees
exactly what the evaluation harness sees."""

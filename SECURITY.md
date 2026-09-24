# Security policy

Please don't open public issues for vulnerabilities. Report them privately through GitHub's
"Report a vulnerability" (Security tab), and include steps to reproduce. We aim to acknowledge within
3 working days.

## Security model (local backend)

| Area | Behaviour |
|---|---|
| Network | Engines and the managed gateway bind to `127.0.0.1` by default. Engines never need to be reachable from outside: clients go through the gateway |
| Adapter loading | vLLM's runtime adapter loading is for trusted networks only. Only Ookami's controller should be able to reach engine ports |
| Secrets | `ookami.yaml` only holds references (`${secret:name}`). Inline credentials are rejected by validation |
| Evaluators | `python`, `command` and `plugin` evaluators run your code with your permissions. Treat `ookami.yaml` like code |
| Data | Stays in your storage. Ookami sends no telemetry |

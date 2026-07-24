# Spec — Fase 2.5: Walking skeleton (deploy + CI/CD)

## 1. Objetivo de la fase

Probar el camino completo de despliegue de punta a punta —Terraform provisiona
la infraestructura en Azure, un contenedor mínimo corre en esa infraestructura,
y un push a `main` lo reconstruye y lo despliega automáticamente— antes de que
exista ningún modelo de ML real. Es deliberadamente "flaco": una sola ruta
`/health`, sin lógica de negocio. Si este esqueleto camina, las fases
siguientes (modelo, agente) se enchufan sobre una base de deploy ya probada.

## 2. Arquitectura: stack de Terraform (PR1)

`infra/terraform/` (estado local, sin backend remoto) provisiona:

| Recurso | Rol |
|---|---|
| `azurerm_resource_group` | Contenedor de todos los recursos (`predictive-monitoring-tool-rg` por defecto) |
| `azurerm_container_registry` (Basic) | Registro de imágenes; nombre único global vía `locals.acr_suffix` (hash determinístico del subscription ID) |
| `azurerm_log_analytics_workspace` + `azurerm_container_app_environment` | Entorno de Container Apps y sus logs |
| `azurerm_container_app` | La app en sí — ingress externo, `target_port=8000`, identidad system-assigned, arranca con una imagen pública placeholder (`mcr.microsoft.com/azuredocs/containerapps-helloworld`) para que `terraform apply` funcione solo, sin depender de CI |
| `azurerm_role_assignment` (AcrPull) | La identidad de la Container App puede tirar imágenes del ACR |
| `azuread_application` + `azuread_service_principal` + `azuread_application_federated_identity_credential` | Confianza OIDC para que GitHub Actions se autentique sin secretos |
| `azurerm_role_assignment` (Contributor, scope = resource group) | Permiso que ese SP federado necesita para desplegar |
| `null_resource.sync_github_client_id_secret` (`local-exec`) | Corre `gh secret set AZURE_CLIENT_ID` con el `gh` CLI local cada vez que cambia el `client_id` de la app (ej. tras un `destroy`/`apply`) |

Todos los nombres, la región y los SKUs son variables (`infra/terraform/variables.tf`) — nada hardcodeado en los `resource` blocks.

## 3. Autenticación OIDC — qué automatiza Terraform y qué queda manual

Terraform crea la App Registration, el Service Principal y la credencial
federada (subject `repo:angelofaraci/predictive-monitoring-tool:ref:refs/heads/main`,
issuer `https://token.actions.githubusercontent.com`) — **no hace falta crear
nada de esto a mano en el portal**. Pero dos pasos quedan fuera del alcance de
un `terraform apply`:

1. **Permisos para correr `terraform apply` la primera vez.** Crear
   `azuread_application`/`azuread_service_principal` requiere un rol de
   Azure AD con privilegios de directorio (p. ej. *Application Administrator*
   o *Cloud Application Administrator*) además del rol de Azure habitual. Quien
   corra el `apply` inicial necesita ese permiso asignado de antemano.
2. **Cargar los outputs como secrets del repo de GitHub.** Terraform no tiene
   (ni debe tener) acceso al repo de GitHub. `AZURE_CLIENT_ID` cambia cada vez
   que se recrea `azuread_application.github_actions` (ej. tras un
   `destroy`/`apply`), así que `null_resource.sync_github_client_id_secret` lo
   sincroniza solo, corriendo `gh secret set` localmente al final de cada
   `terraform apply` (requiere `gh` instalado y autenticado en la máquina que
   corre `apply`). `AZURE_TENANT_ID` y `AZURE_SUBSCRIPTION_ID` no cambian nunca
   (son del tenant/subscription, no de un recurso creado), así que se cargan
   una sola vez a mano:

   ```bash
   terraform -chdir=infra/terraform output -raw azure_tenant_id
   terraform -chdir=infra/terraform output -raw azure_subscription_id

   gh secret set AZURE_TENANT_ID --body "<valor>"
   gh secret set AZURE_SUBSCRIPTION_ID --body "<valor>"
   ```

Además, antes de aplicar, verificar que la región elegida (`East US` por
defecto) soporte Container Apps y que los resource providers necesarios
(`Microsoft.App`, `Microsoft.OperationalInsights`, `Microsoft.ContainerRegistry`)
estén registrados en la subscription.

## 4. La app y el pipeline (PR2)

- `src/predictive_monitoring_tool/api/main.py`: FastAPI mínima, una sola ruta
  `GET /health` -> `{"status": "ok"}`, desarrollada con TDD estricto
  (`tests/test_health.py` escrito antes de que el módulo existiera).
- `Dockerfile`: single-stage sobre `ghcr.io/astral-sh/uv:python3.14-bookworm-slim`,
  `uv sync --frozen --no-dev`, corre `uvicorn` en el puerto 8000 (mismo
  `target_port` que la Container App de Terraform).
- `.github/workflows/deploy.yml`: en push a `main` (o disparo manual),
  autentica a Azure vía OIDC, resuelve el login server del ACR (su nombre
  no es determinístico, así que se busca en runtime con `az acr list`),
  buildea y pushea la imagen taggeada con `github.sha`, corre
  `az containerapp update --image ...` y por último hace `curl` a `/health`
  con reintentos (una revisión nueva de Container Apps tarda unos segundos
  en estar lista).

### Cómo disparar un deploy

```bash
git push origin main
# o, sin nuevo commit:
gh workflow run deploy.yml
```

## 5. Estructura de archivos nuevos

```
Dockerfile
.github/workflows/deploy.yml
src/predictive_monitoring_tool/
└── api/
    ├── __init__.py
    └── main.py
tests/
└── test_health.py
```

## 6. Fuera de alcance en esta fase

No hay modelo, ni agente, ni MCP, ni backend remoto de Terraform. El estado de
Terraform es local (`Fase 2.5`); un backend remoto con locking queda para una
fase posterior si el equipo crece.

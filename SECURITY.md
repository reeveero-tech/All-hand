# AGOS Agent Civilization - Security Documentation

**Version:** 1.0  
**Last Updated:** 2026-07-23

---

## Overview

This document outlines the security features implemented in the AGOS Agent Civilization platform and provides guidance for securing your deployment.

---

## Security Features

### 1. API Key Authentication (Optional)

API Key authentication protects sensitive endpoints from unauthorized access.

**Configuration:**
```bash
# Generate a secure API key
python -c "import secrets; print(secrets.token_urlsafe(32))"

# Set in environment
export AGOS_API_KEY=your_generated_key
```

**Usage:**
```bash
# Include API key in requests
curl -X POST https://your-domain.com/api/tasks \
  -H "X-API-Key: your_generated_key" \
  -H "Content-Type: application/json" \
  -d '{"specialization":"analyst","prompt":"Analyze data"}'
```

**Protected Endpoints:**
- `POST /api/agents/external` - Register external agents
- `DELETE /api/agents/external/{name}` - Remove external agents

**Note:** Authentication is automatically enabled when `AGOS_API_KEY` is set.

---

### 2. CORS Configuration

Cross-Origin Resource Sharing (CORS) controls which domains can access your API.

**Configuration:**
```bash
# Allow specific origins (recommended for production)
export AGOS_ALLOWED_ORIGINS=https://your-domain.com,https://app.your-domain.com

# Allow all origins (development only)
export AGOS_ALLOWED_ORIGINS=*
```

**Security Warning:**
> ⚠️ Setting `AGOS_ALLOWED_ORIGINS=*` allows any website to make requests to your API. Always use specific origins in production.

**Best Practices:**
1. List only domains that need access
2. Use HTTPS for all origins
3. Regularly review allowed origins

---

### 3. Rate Limiting

Rate limiting protects against denial-of-service (DoS) attacks and excessive resource usage.

**Configuration:**
```bash
# Limit to 100 requests per 60 seconds per client IP
export AGOS_RATE_LIMIT_REQUESTS=100
export AGOS_RATE_LIMIT_WINDOW=60
```

**Default Values:**
| Setting | Default | Description |
|---------|---------|-------------|
| `AGOS_RATE_LIMIT_REQUESTS` | 100 | Max requests per window |
| `AGOS_RATE_LIMIT_WINDOW` | 60 | Window size in seconds |

**Response Headers:**
When rate limited, the API returns HTTP 429:
```json
{
  "detail": "Rate limit exceeded. Max 100 requests per 60s"
}
```

---

### 4. Input Validation

All API inputs are validated using Pydantic models:

**Task Request:**
```python
class TaskRequest(BaseModel):
    specialization: str  # Must be valid specialization
    prompt: str          # 1-20,000 characters
    data: Optional[Any]  # Optional payload
```

**Validation:**
- Prompt length: 1-20,000 characters
- Specialization: Must be one of 20 valid specializations
- URL validation for external agents

---

### 5. Logging and Monitoring

Structured logging helps detect and investigate security incidents.

**Configuration:**
```python
# Structured log format
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s - %(funcName)s:%(lineno)d"
)
```

**Security Events Logged:**
- Task creation
- External agent registration/removal
- Authentication failures
- Rate limit violations

---

## Environment Variables Summary

| Variable | Required | Default | Security |
|----------|----------|---------|----------|
| `AGOS_API_KEY` | For auth | None | HIGH |
| `AGOS_ALLOWED_ORIGINS` | No | `*` | HIGH |
| `AGOS_RATE_LIMIT_REQUESTS` | No | 100 | MEDIUM |
| `AGOS_RATE_LIMIT_WINDOW` | No | 60 | MEDIUM |
| `OPENROUTER_API_KEY` | For AI | None | HIGH |
| `SUPABASE_ANON_KEY` | For persistence | None | MEDIUM |

---

## Production Checklist

Before deploying to production, ensure:

- [ ] Set `AGOS_API_KEY` to a secure value
- [ ] Configure `AGOS_ALLOWED_ORIGINS` with specific domains
- [ ] Review and adjust rate limits
- [ ] Enable Supabase for persistent storage
- [ ] Use HTTPS for all connections
- [ ] Review logs regularly
- [ ] Enable monitoring/alerting

---

## Known Limitations

### Current Limitations

| Issue | Status | Mitigation |
|-------|--------|------------|
| No JWT/OAuth | Planned | Use API key for now |
| No RBAC | Planned | API key is admin-only |
| No audit trail | Planned | Logs are the audit trail |
| Code execution sandbox | Planned | Run in isolated environment |

### Future Enhancements

1. **JWT Authentication** - User-level auth with roles
2. **RBAC** - Role-based access control
3. **Audit Logging** - Persistent audit trail
4. **IP Whitelisting** - Additional access control
5. **Code Execution Sandboxing** - Docker/seccomp

---

## Incident Response

### If Compromised

1. **Immediate Actions:**
   - Revoke API key: Set new `AGOS_API_KEY`
   - Block malicious IPs via firewall
   - Review access logs

2. **Investigation:**
   - Check logs for unauthorized access
   - Review task history
   - Identify affected data

3. **Recovery:**
   - Rotate all API keys
   - Update CORS origins
   - Enable additional monitoring

### Contact

For security vulnerabilities, please report to the repository maintainers.

---

## Best Practices

1. **Never commit secrets** to version control
2. **Use environment variables** for all secrets
3. **Regularly rotate** API keys
4. **Monitor logs** for suspicious activity
5. **Keep dependencies** updated
6. **Use HTTPS** in production
7. **Follow principle of least privilege**

---

## Compliance

AGOS is designed with security in mind but is not currently certified for:
- SOC 2
- HIPAA
- PCI-DSS
- GDPR (without additional configuration)

For enterprise compliance requirements, contact the development team.

---

*Last updated: 2026-07-23*

---

## الوضع الأمني المقاس (تدقيق M0 — 2026-10-12)

إرشادات الضبط أعلاه تصف الوضع المنشود. قاس تدقيق M0 سلوك الكود الفعلي عند `be480820`، ويختلف عن هذه الوثيقة في نقاط جوهرية:

- `verify_api_key()` مُعرّفة ولا تُستدعى في جسم أي مسار؛
  و`POST /api/tasks` و`POST /api/chat` تستقبل المفتاح دون فحصه.
- `POST/DELETE /api/agents/external*` يفحصان فقط هل المصادقة مفعلة (`ENABLE_AUTH`) — ولا يتحققان من قيمة المفتاح حتى عند تفعيلها.
- مسارات `/civilization/*` بلا أي معلمة أمنية.
- `GET /health` يحسب رمز 503 لكنه لا يعيده أبداً (200 دائماً).
- CORS الافتراضي `*` مع `allow_credentials=True`.

المرجع المعتمد للتوثيق الأمني المقيس أصبح
[docs/governance/SECURITY.md](docs/governance/SECURITY.md)، والمعالجة متتبعة كبند خارطة R-004. تظل إرشادات الضبط في هذه الوثيقة صالحة لغرضها الأصلي (كيفية ضبط المتغيرات عند النشر).

# Plan de nettoyage de la base de données

> Context: BD de dev avec plein de données de test. On veut faire le ménage tout en gardant ce qui compte.

---

## Tables existantes (12 migrations)

| Table | Contenu probable | Action |
|---|---|---|
| `agent_configs` | Agents créés à la main | **GARDER** |
| `users` | Ton compte + comptes test | Garder le tien, supprimer les fakes |
| `workspaces` | Workspaces créés | Garder les vrais |
| `api_keys` | Clés API actives | Garder les valides |
| `conversations` + `messages` | Mix de tests + vraies convos | **Trier** (voir ci-dessous) |
| `tasks` | Tasks de test Celery | TRUNCATE safe |
| `audit_logs` | Logs de test | TRUNCATE safe |
| `memory_sources` | Sources mémoire LangGraph | Vérifier avant de toucher |
| LangGraph checkpoints | États des agents | TRUNCATE si tu veux repartir à zéro |

---

## Étape 1 — Backup nostalgique (avant de rien toucher)

```bash
# SSH sur le serveur
ssh user@192.168.1.10

# Dump complet avec timestamp
docker exec hiveflows-postgres-1 pg_dump -U postgres hiveflows \
  > ~/hiveflows/backup_$(date +%Y%m%d_%H%M).sql
```

---

## Étape 2 — Inspecter ce qu'il y a (curiosité)

```sql
-- Combien de chaque?
SELECT 'conversations' as t, COUNT(*) FROM conversations
UNION ALL SELECT 'messages', COUNT(*) FROM messages
UNION ALL SELECT 'tasks', COUNT(*) FROM tasks
UNION ALL SELECT 'agent_configs', COUNT(*) FROM agent_configs
UNION ALL SELECT 'audit_logs', COUNT(*) FROM audit_logs
UNION ALL SELECT 'users', COUNT(*) FROM users
UNION ALL SELECT 'workspaces', COUNT(*) FROM workspaces;

-- Voir les conversations (pour décider lesquelles garder)
SELECT id, agent_id, created_at,
       (SELECT COUNT(*) FROM messages WHERE conversation_id = c.id) as msg_count
FROM conversations c
ORDER BY created_at DESC;

-- Voir les agents configurés
SELECT id, name, is_active, created_at FROM agent_configs ORDER BY created_at;

-- Voir les users
SELECT id, username, email, created_at FROM users ORDER BY created_at;
```

Pour lancer ces requêtes:
```bash
docker exec -it hiveflows-postgres-1 psql -U postgres hiveflows
```

---

## Étape 3 — Décisions à prendre

Avant de supprimer quoi que ce soit, noter ici:

- [ ] Conversations à GARDER (copier les IDs ici): ___
- [ ] Users fictifs à SUPPRIMER: ___
- [ ] Workspaces à SUPPRIMER: ___
- [ ] Agent configs à désactiver (pas supprimer): ___

---

## Étape 4 — Nettoyage (SQL à run dans l'ordre)

```sql
-- 1. Vider les tables "junk" sans hésitation
TRUNCATE TABLE audit_logs RESTART IDENTITY CASCADE;
TRUNCATE TABLE tasks RESTART IDENTITY CASCADE;

-- 2. Supprimer les conversations de test (garder les bonnes IDs notées à l'étape 3)
DELETE FROM conversations
WHERE id NOT IN ('...uuid1...', '...uuid2...');  -- remplacer par les IDs à garder
-- (les messages sont supprimés automatiquement via CASCADE)

-- 3. Supprimer les users de test
DELETE FROM users WHERE username IN ('test', 'testuser', 'demo', ...);

-- 4. Vider les checkpoints LangGraph (repart à zéro mémoire agents)
-- ⚠️ Attention: efface la mémoire cross-conversation des agents
TRUNCATE TABLE checkpoints RESTART IDENTITY CASCADE;
TRUNCATE TABLE checkpoint_writes RESTART IDENTITY CASCADE;
TRUNCATE TABLE checkpoint_blobs RESTART IDENTITY CASCADE;
```

---

## Étape 5 — Vérification post-nettoyage

```sql
-- Recompter pour confirmer
SELECT 'conversations' as t, COUNT(*) FROM conversations
UNION ALL SELECT 'messages', COUNT(*) FROM messages
UNION ALL SELECT 'tasks', COUNT(*) FROM tasks
UNION ALL SELECT 'users', COUNT(*) FROM users
UNION ALL SELECT 'audit_logs', COUNT(*) FROM audit_logs;
```

---

## Option nucléaire (si tu veux repartir à zéro complet)

```bash
# Recrée la BD entière à partir des migrations
docker exec hiveflows-postgres-1 dropdb -U postgres hiveflows
docker exec hiveflows-postgres-1 createdb -U postgres hiveflows
docker compose exec core alembic upgrade head
```

⚠️ Ça efface TOUT. Utiliser seulement après un backup confirmé.

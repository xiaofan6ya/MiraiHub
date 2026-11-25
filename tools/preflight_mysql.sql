-- MiraiHub 上线前 MySQL 自查（只读）
--
-- 用法：
--   mysql --default-character-set=utf8mb4 -h HOST -u USER -p DBNAME < tools/preflight_mysql.sql
-- 或者先进 mysql 客户端再 `source tools/preflight_mysql.sql`。
--
-- 约束：本文件**只允许 SELECT / SHOW**。不要在这里加 UPDATE / ALTER / DELETE：
-- 上线前自查必须能在生产只读账号下跑完，改数据要另走迁移。
--
-- 每一项输出都有 `check_name` + `expect` + `actual`，人眼一眼能判断对错；
-- 标 `[FAIL]` 的行必须先处理再切流。

SELECT 'server' AS check_name, 'MySQL >= 5.7 / 8.0' AS expect,
       CONCAT(VERSION(), ' / ', @@character_set_server) AS actual;

-- 1) 字符集：中文平台名、模型描述都靠 utf8mb4
SELECT 'db_charset' AS check_name, 'utf8mb4' AS expect,
       DEFAULT_CHARACTER_SET_NAME AS actual
  FROM information_schema.SCHEMATA
 WHERE SCHEMA_NAME = DATABASE();

-- 2) 金额列必须是 DECIMAL(18,6)：FLOAT/DOUBLE 会在累加时掉精度
SELECT 'money_columns' AS check_name, 'DECIMAL(18,6)' AS expect,
       CONCAT(TABLE_NAME, '.', COLUMN_NAME, ' = ', COLUMN_TYPE,
              IF(DATA_TYPE IN ('float', 'double'), '  [FAIL]', '')) AS actual
  FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA = DATABASE()
   AND COLUMN_NAME IN ('credits', 'credit_used', 'credit_limit', 'balance',
                       'balance_total', 'balance_remain', 'amount', 'real_credits',
                       'delta', 'total_credits', 'cost')
 ORDER BY TABLE_NAME, COLUMN_NAME;

-- 3) 关键表是否都在（缺表说明迁移没跑完）
SELECT 'core_tables' AS check_name, '6 tables' AS expect,
       GROUP_CONCAT(TABLE_NAME ORDER BY TABLE_NAME) AS actual
  FROM information_schema.TABLES
 WHERE TABLE_SCHEMA = DATABASE()
   AND TABLE_NAME IN ('accounts', 'api_keys', 'usage_logs', 'model_configs',
                      'system_settings', 'schedules');

-- 4) usage_logs 计数索引：概览/统计/日志页全靠它，缺了会全表扫
SELECT 'usage_logs_index' AS check_name, 'ix_usage_logs_overview present' AS expect,
       IF(COUNT(*) > 0, 'present', 'MISSING  [FAIL]') AS actual
  FROM information_schema.STATISTICS
 WHERE TABLE_SCHEMA = DATABASE()
   AND TABLE_NAME = 'usage_logs'
   AND INDEX_NAME = 'ix_usage_logs_overview';

-- 5) schedules.realm（第 5 轮 C3 的平台区迁移）：缺列时按平台区排定时任务会报错
SELECT 'schedules.realm' AS check_name, 'column present' AS expect,
       IF(COUNT(*) > 0, 'present', 'MISSING  [FAIL]') AS actual
  FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA = DATABASE()
   AND TABLE_NAME = 'schedules'
   AND COLUMN_NAME = 'realm';

-- 6) 表体量：超过 500 万行的 usage_logs 要确认备份窗口与 rollup 是否已开
SELECT 'usage_logs_rows' AS check_name, '< 5,000,000（否则确认备份窗口）' AS expect,
       FORMAT(TABLE_ROWS, 0) AS actual
  FROM information_schema.TABLES
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'usage_logs';

-- 7) 金额列现有 NULL：NULL 参与 SUM 会被 coalesce 兜住，但 key 额度累加仍要知道有多少
-- 表不存在时不能直接查：用 IF() 分流，避免整份脚本因单表缺失中断。
SET @has_api_keys := (SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'api_keys');
SET @api_keys_missing := IF(@has_api_keys > 0, '', 'table missing: api_keys');
SELECT @api_keys_missing AS precheck_api_keys;
SET @raw_api_keys := 'SELECT ''api_keys.credit_used_null'' AS check_name, ''0'' AS expect,
       COUNT(*) AS actual
  FROM api_keys';
SET @stmt_api_keys := IF(@has_api_keys > 0, @raw_api_keys, 'SELECT ''skipped: api_keys missing'' AS actual');
PREPARE s_api_keys FROM @stmt_api_keys;
EXECUTE s_api_keys;
DEALLOCATE PREPARE s_api_keys;

-- 8) 账号余额为负（除 0 之外不应出现；出现说明有 bug 在写负数）
-- 表不存在时不能直接查：用 IF() 分流，避免整份脚本因单表缺失中断。
SET @has_accounts := (SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'accounts');
SET @accounts_missing := IF(@has_accounts > 0, '', 'table missing: accounts');
SELECT @accounts_missing AS precheck_accounts;
SET @raw_accounts := 'SELECT ''accounts.negative_balance'' AS check_name, ''0'' AS expect,
       COUNT(*) AS actual
  FROM accounts';
SET @stmt_accounts := IF(@has_accounts > 0, @raw_accounts, 'SELECT ''skipped: accounts missing'' AS actual');
PREPARE s_accounts FROM @stmt_accounts;
EXECUTE s_accounts;
DEALLOCATE PREPARE s_accounts;

-- 9) 会话/审计类大表是否已清理（可选，仅提示）
-- 表不存在时不能直接查：用 IF() 分流，避免整份脚本因单表缺失中断。
SET @has_usage_logs := (SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'usage_logs');
SET @usage_logs_missing := IF(@has_usage_logs > 0, '', 'table missing: usage_logs');
SELECT @usage_logs_missing AS precheck_usage_logs;
SET @raw_usage_logs := 'SELECT ''usage_logs_oldest'' AS check_name, ''有数据即可（用于估算保留期）'' AS expect,
       COALESCE(DATE_FORMAT(MIN(created_at), ''%Y-%m-%d''), ''empty'') AS actual';
SET @stmt_usage_logs := IF(@has_usage_logs > 0, @raw_usage_logs, 'SELECT ''skipped: usage_logs missing'' AS actual');
PREPARE s_usage_logs FROM @stmt_usage_logs;
EXECUTE s_usage_logs;
DEALLOCATE PREPARE s_usage_logs;

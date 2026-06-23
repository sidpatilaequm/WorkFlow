-- SQL Migration Script
-- Schema: multimedia_governance
-- Targets: Bring MySQL database tables in sync with updated backend SQLAlchemy models

-- 1. Create standalone_messages table if it does not exist
CREATE TABLE IF NOT EXISTS multimedia_governance.standalone_messages (
    `id`                      INT AUTO_INCREMENT PRIMARY KEY,
    `sender_id`               INT NULL,
    `to_emails`               JSON NOT NULL,   -- list of email strings
    `subject`                 VARCHAR(300) NULL,
    `message`                 TEXT NOT NULL,
    `context`                 JSON NULL,       -- flat key-value dict for {{}} rendering
    `reminder_interval_hours` INT NULL,        -- NULL = one-shot
    `max_reminders`           INT NULL,        -- NULL = unlimited until manually stopped
    `reminders_sent`          INT DEFAULT 0,
    `last_sent_at`            DATETIME NULL,
    `is_active`               BOOLEAN DEFAULT TRUE,
    `created_at`              DATETIME DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT `fk_standalone_messages_sender` 
        FOREIGN KEY (`sender_id`) REFERENCES multimedia_governance.user_details(`userId`)
        ON DELETE SET NULL
);

-- 2. Update workflows table with redirects and message variables
ALTER TABLE multimedia_governance.workflows 
    ADD COLUMN IF NOT EXISTS `success_redirect_url` VARCHAR(500) NULL,
    ADD COLUMN IF NOT EXISTS `failure_redirect_url` VARCHAR(500) NULL,
    ADD COLUMN IF NOT EXISTS `message_variables` JSON NULL;

-- 3. Update workflow_stages table with button labels and parallel execution group
ALTER TABLE multimedia_governance.workflow_stages 
    ADD COLUMN IF NOT EXISTS `approve_label` VARCHAR(50) NULL,
    ADD COLUMN IF NOT EXISTS `reject_label` VARCHAR(50) NULL,
    ADD COLUMN IF NOT EXISTS `parallel_group` INT NULL;

-- 4. Update workflow_requests table with a frozen workflow configuration snapshot
ALTER TABLE multimedia_governance.workflow_requests 
    ADD COLUMN IF NOT EXISTS `workflow_snapshot` JSON NULL;

-- 5. Update approver_group_members table to support optional reviewers
ALTER TABLE multimedia_governance.approver_group_members 
    ADD COLUMN IF NOT EXISTS `is_optional` BOOLEAN NULL DEFAULT FALSE;

-- 6. Update approval_actions table to support uploaded files on approval/rejection
ALTER TABLE multimedia_governance.approval_actions
    ADD COLUMN IF NOT EXISTS `document_name` VARCHAR(300) NULL,
    ADD COLUMN IF NOT EXISTS `document_url` VARCHAR(500) NULL;

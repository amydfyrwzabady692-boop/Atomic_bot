-- اسکیما اختصاصی ربات Atomic (جدا از tasino)
CREATE TABLE IF NOT EXISTS "Users" (
    "Id" SERIAL PRIMARY KEY,
    "password" VARCHAR(128) NOT NULL DEFAULT '',
    "Username" VARCHAR(150) UNIQUE NOT NULL,
    "Email" VARCHAR(254) NOT NULL DEFAULT '',
    "FirstName" VARCHAR(150) NOT NULL DEFAULT '',
    "LastName" VARCHAR(150) NOT NULL DEFAULT '',
    "IsStaff" BOOLEAN NOT NULL DEFAULT false,
    "IsActive" BOOLEAN NOT NULL DEFAULT true,
    "IsSuperUser" BOOLEAN NOT NULL DEFAULT false,
    "TelegramId" VARCHAR(64),
    "TelegramUsername" VARCHAR(150) NOT NULL DEFAULT '',
    "IsTelegramPremium" BOOLEAN NOT NULL DEFAULT false,
    "IsBlocked" BOOLEAN NOT NULL DEFAULT false,
    "BlockedReason" VARCHAR(255) NOT NULL DEFAULT '',
    "BlockedAt" TIMESTAMPTZ,
    "KycStatus" VARCHAR(20) NOT NULL DEFAULT 'none',
    "KycCode" VARCHAR(32) NOT NULL DEFAULT '',
    "KycVerifiedAt" TIMESTAMPTZ,
    "KycRejectReason" VARCHAR(255) NOT NULL DEFAULT '',
    "DateJoined" TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_users_telegram ON "Users" ("TelegramId");

CREATE TABLE IF NOT EXISTS "GemPackages" (
    "Id" SERIAL PRIMARY KEY,
    "Title" VARCHAR(255) NOT NULL,
    "Amount" INTEGER NOT NULL CHECK ("Amount" > 0),
    "BonusAmount" INTEGER NOT NULL DEFAULT 0,
    "Price" INTEGER NOT NULL CHECK ("Price" > 0),
    "OldPrice" INTEGER NULL,
    "PlanType" VARCHAR(20) NOT NULL DEFAULT 'once',
    "PurchaseType" VARCHAR(30) NOT NULL DEFAULT 'by_id',
    "AutoDeliver" BOOLEAN NOT NULL DEFAULT true,
    "G2BulkCatalogueName" VARCHAR(100),
    "Stock" INTEGER NOT NULL DEFAULT 9999 CHECK ("Stock" >= 0),
    "IsAvailable" BOOLEAN NOT NULL DEFAULT true,
    "IsActive" BOOLEAN NOT NULL DEFAULT true,
    "SortOrder" INTEGER NOT NULL DEFAULT 0 CHECK ("SortOrder" >= 0),
    "CreatedAt" TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS "Orders" (
    "Id" SERIAL PRIMARY KEY,
    "UserId" INTEGER REFERENCES "Users"("Id"),
    "FullName" VARCHAR(255) NOT NULL DEFAULT '',
    "Email" VARCHAR(254) NOT NULL DEFAULT '',
    "Phone" VARCHAR(30) NOT NULL DEFAULT '',
    "TelegramId" VARCHAR(64),
    "TotalAmount" INTEGER NOT NULL CHECK ("TotalAmount" > 0),
    "DiscountAmount" INTEGER NOT NULL DEFAULT 0 CHECK ("DiscountAmount" >= 0 AND "DiscountAmount" < "TotalAmount"),
    "PaymentMethod" VARCHAR(30) NOT NULL DEFAULT 'pending',
    "PaymentAuthority" VARCHAR(100),
    "PaymentExpectedAmount" INTEGER CHECK ("PaymentExpectedAmount" IS NULL OR "PaymentExpectedAmount" > 0),
    "PaymentVerifiedAt" TIMESTAMPTZ,
    "PaymentRefId" VARCHAR(100),
    "DeliveryUserNotifiedAt" TIMESTAMPTZ,
    "DeliveryAdminNotifiedAt" TIMESTAMPTZ,
    "PaymentExpiresAt" TIMESTAMPTZ NOT NULL DEFAULT (now() + interval '15 minutes'),
    "WalletPaid" INTEGER NOT NULL DEFAULT 0 CHECK ("WalletPaid" >= 0),
    "Status" VARCHAR(30) NOT NULL DEFAULT 'pending',
    "CreatedAt" TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_orders_payment_authority
ON "Orders" ("PaymentAuthority")
WHERE "PaymentAuthority" IS NOT NULL AND "PaymentAuthority" <> '';
CREATE INDEX IF NOT EXISTS idx_orders_payment_expiry
ON "Orders" ("PaymentExpiresAt")
WHERE "Status"='pending' AND "PaymentVerifiedAt" IS NULL;
CREATE INDEX IF NOT EXISTS idx_orders_processing_verified
ON "Orders" ("PaymentVerifiedAt")
WHERE "Status"='processing' AND "PaymentVerifiedAt" IS NOT NULL;

CREATE TABLE IF NOT EXISTS "OrderItems" (
    "Id" SERIAL PRIMARY KEY,
    "OrderId" INTEGER NOT NULL REFERENCES "Orders"("Id") ON DELETE CASCADE,
    "ProductId" INTEGER NULL,
    "ProductName" VARCHAR(255) NOT NULL,
    "Price" INTEGER NOT NULL CHECK ("Price" > 0),
    "Quantity" INTEGER NOT NULL DEFAULT 1 CHECK ("Quantity" > 0)
);

CREATE TABLE IF NOT EXISTS "GemOrderInfo" (
    "Id" SERIAL PRIMARY KEY,
    "OrderId" INTEGER NOT NULL REFERENCES "Orders"("Id") ON DELETE CASCADE,
    "OrderItemId" INTEGER REFERENCES "OrderItems"("Id") ON DELETE SET NULL,
    "GemPackageId" INTEGER REFERENCES "GemPackages"("Id"),
    "PurchaseType" VARCHAR(30) NOT NULL DEFAULT 'by_id',
    "TelegramId" VARCHAR(64),
    "GameUID" VARCHAR(64),
    "PlayerName" VARCHAR(255),
    "LoginMethod" VARCHAR(30),
    "LoginEmail" VARCHAR(255),
    "LoginPassword" VARCHAR(255),
    "BackupCode" TEXT,
    "CredentialCiphertext" TEXT,
    "CredentialStatus" VARCHAR(30) NOT NULL DEFAULT '',
    "CredentialTwoFactorEnabled" BOOLEAN,
    "CredentialAdminNote" VARCHAR(500) NOT NULL DEFAULT '',
    "CredentialViewedAt" TIMESTAMPTZ,
    "CredentialDeletedAt" TIMESTAMPTZ,
    "CredentialUpdatedAt" TIMESTAMPTZ,
    "G2BulkOrderId" VARCHAR(50),
    "G2BulkStatus" VARCHAR(30),
    "G2BulkSubmittedAt" TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS "OrderProfitSnapshots" (
    "Id" BIGSERIAL PRIMARY KEY,
    "OrderId" INTEGER NOT NULL REFERENCES "Orders"("Id") ON DELETE CASCADE,
    "GemOrderInfoId" INTEGER UNIQUE NOT NULL REFERENCES "GemOrderInfo"("Id") ON DELETE CASCADE,
    "SaleAmountToman" INTEGER NOT NULL CHECK ("SaleAmountToman" > 0),
    "SupplierCostUsd" NUMERIC(18,6) NOT NULL CHECK ("SupplierCostUsd" > 0),
    "UsdTomanRate" INTEGER CHECK ("UsdTomanRate" IS NULL OR "UsdTomanRate" > 0),
    "SupplierCostToman" INTEGER CHECK ("SupplierCostToman" IS NULL OR "SupplierCostToman" > 0),
    "GrossProfitToman" INTEGER,
    "FxSource" VARCHAR(80) NOT NULL DEFAULT '',
    "FxObservedMs" BIGINT,
    "CapturedAt" TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_profit_snapshots_captured
ON "OrderProfitSnapshots" ("CapturedAt" DESC);

CREATE TABLE IF NOT EXISTS "Wallets" (
    "Id" SERIAL PRIMARY KEY,
    "UserId" INTEGER UNIQUE NOT NULL REFERENCES "Users"("Id") ON DELETE CASCADE,
    "Balance" INTEGER NOT NULL DEFAULT 0 CHECK ("Balance" >= 0),
    "UpdatedAt" TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS "WalletTransactions" (
    "Id" SERIAL PRIMARY KEY,
    "WalletId" INTEGER NOT NULL REFERENCES "Wallets"("Id") ON DELETE CASCADE,
    "Amount" INTEGER NOT NULL CHECK ("Amount" > 0),
    "Kind" VARCHAR(10) NOT NULL DEFAULT 'charge',
    "Description" VARCHAR(255) NOT NULL DEFAULT '',
    "Authority" VARCHAR(100),
    "IsPaid" BOOLEAN NOT NULL DEFAULT false,
    "PaymentExpectedAmount" INTEGER,
    "PaymentVerifiedAt" TIMESTAMPTZ,
    "PaymentRefId" VARCHAR(100),
    "CreatedAt" TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_wallet_transactions_authority
ON "WalletTransactions" ("Authority")
WHERE "Authority" IS NOT NULL AND "Authority" <> '';
CREATE UNIQUE INDEX IF NOT EXISTS uq_wallet_transactions_payment_ref
ON "WalletTransactions" ("PaymentRefId")
WHERE "PaymentRefId" IS NOT NULL AND "PaymentRefId" <> '';

CREATE TABLE IF NOT EXISTS "PaymentAttempts" (
    "Id" BIGSERIAL PRIMARY KEY,
    "OrderId" INTEGER REFERENCES "Orders"("Id") ON DELETE SET NULL,
    "WalletTransactionId" INTEGER REFERENCES "WalletTransactions"("Id") ON DELETE SET NULL,
    "TelegramId" VARCHAR(64),
    "Provider" VARCHAR(30) NOT NULL,
    "Event" VARCHAR(40) NOT NULL,
    "Status" VARCHAR(20) NOT NULL,
    "Amount" INTEGER,
    "Authority" VARCHAR(100),
    "RefId" VARCHAR(100),
    "Message" VARCHAR(500) NOT NULL DEFAULT '',
    "CreatedAt" TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_payment_attempts_status_created
ON "PaymentAttempts" ("Status", "CreatedAt" DESC);
CREATE INDEX IF NOT EXISTS idx_payment_attempts_order
ON "PaymentAttempts" ("OrderId", "CreatedAt" DESC);

-- PaymentReceipts باید قبل از ایندکس pending ساخته شود (باگ قبلی init تازه)
CREATE TABLE IF NOT EXISTS "PaymentReceipts" (
    "Id" SERIAL PRIMARY KEY,
    "OrderId" INTEGER REFERENCES "Orders"("Id") ON DELETE CASCADE,
    "WalletTransactionId" INTEGER REFERENCES "WalletTransactions"("Id") ON DELETE CASCADE,
    "TelegramId" VARCHAR(64),
    "ReceiptType" VARCHAR(20) NOT NULL DEFAULT 'order',
    "FileId" TEXT NOT NULL DEFAULT '',
    "Text" TEXT NOT NULL DEFAULT '',
    "Status" VARCHAR(20) NOT NULL DEFAULT 'pending',
    "CreatedAt" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "ReviewedAt" TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_payment_receipts_pending
ON "PaymentReceipts" ("CreatedAt")
WHERE "Status"='pending';

CREATE TABLE IF NOT EXISTS "AdminAuditLogs" (
    "Id" BIGSERIAL PRIMARY KEY,
    "AdminTelegramId" VARCHAR(64) NOT NULL,
    "Action" VARCHAR(80) NOT NULL,
    "TargetType" VARCHAR(40) NOT NULL DEFAULT '',
    "TargetId" VARCHAR(100) NOT NULL DEFAULT '',
    "Details" VARCHAR(500) NOT NULL DEFAULT '',
    "CreatedAt" TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_admin_audit_created
ON "AdminAuditLogs" ("CreatedAt" DESC);

CREATE TABLE IF NOT EXISTS "SupportTickets" (
    "Id" SERIAL PRIMARY KEY,
    "UserId" INTEGER REFERENCES "Users"("Id"),
    "Subject" VARCHAR(255) NOT NULL,
    "Category" VARCHAR(50) NOT NULL DEFAULT 'other',
    "Priority" VARCHAR(20) NOT NULL DEFAULT 'normal',
    "Message" TEXT NOT NULL,
    "Status" VARCHAR(20) NOT NULL DEFAULT 'open',
    "TelegramId" VARCHAR(64),
    "CreatedAt" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "UpdatedAt" TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS "TicketMessages" (
    "Id" SERIAL PRIMARY KEY,
    "TicketId" INTEGER NOT NULL REFERENCES "SupportTickets"("Id") ON DELETE CASCADE,
    "Sender" VARCHAR(20) NOT NULL,
    "Text" TEXT NOT NULL,
    "CreatedAt" TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS "ForcedJoinChannels" (
    "Id" SERIAL PRIMARY KEY,
    "ChatId" VARCHAR(100) UNIQUE NOT NULL,
    "Title" VARCHAR(150) NOT NULL DEFAULT '',
    "InviteUrl" TEXT NOT NULL,
    "IsActive" BOOLEAN NOT NULL DEFAULT true,
    "CreatedAt" TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO "ForcedJoinChannels" ("ChatId","Title","InviteUrl")
VALUES ('@Omid_AtomicFF','کانال امید اتمیک','https://t.me/Omid_AtomicFF')
ON CONFLICT ("ChatId") DO NOTHING;

-- بسته‌های جم ME (مثل سایت)
INSERT INTO "GemPackages"
("Title", "Amount", "BonusAmount", "Price", "OldPrice", "PlanType", "PurchaseType",
 "AutoDeliver", "G2BulkCatalogueName", "Stock", "IsAvailable", "IsActive")
VALUES
('🎯 لول‌آپ سطح 6', 6, 0, 65000, NULL, 'once', 'by_id', true, 'Level Up Package - Level 6', 9999, true, true),
('🎯 لول‌آپ سطح 10', 10, 0, 110000, NULL, 'once', 'by_id', true, 'Level Up Package - Level 10', 9999, true, true),
('🎯 لول‌آپ سطح 15', 15, 0, 110000, NULL, 'once', 'by_id', true, 'Level Up Package - Level 15', 9999, true, true),
('🎯 لول‌آپ سطح 20', 20, 0, 110000, NULL, 'once', 'by_id', true, 'Level Up Package - Level 20', 9999, true, true),
('🎯 لول‌آپ سطح 25', 25, 0, 110000, NULL, 'once', 'by_id', true, 'Level Up Package - Level 25', 9999, true, true),
('🎯 لول‌آپ سطح 30', 30, 0, 172000, NULL, 'once', 'by_id', true, 'Level Up Package - Level 30', 9999, true, true),
('💎 110 جم', 110, 0, 191000, NULL, 'once', 'by_id', true, '110', 9999, true, true),
('💎 231 جم', 231, 0, 382000, NULL, 'once', 'by_id', true, '231', 9999, true, true),
('📅 بسته هفتگی', 90001, 0, 430000, NULL, 'once', 'by_id', true, 'Weekly Membership', 9999, true, true),
('🏆 بویاه پس', 90002, 0, 640000, NULL, 'once', 'by_id', true, 'Booyah Pass', 9999, true, true),
('💎 583 جم', 583, 0, 956000, NULL, 'once', 'by_id', true, '583', 9999, true, true),
('💎 1188 جم', 1188, 0, 1913000, NULL, 'once', 'by_id', true, '1188', 9999, true, true),
('📆 بسته ماهانه', 90003, 0, 2106000, NULL, 'once', 'by_id', true, 'Monthly Membership', 9999, true, true),
('💎 2420 جم', 2420, 0, 3824000, NULL, 'once', 'by_id', true, '2420', 9999, true, true)
ON CONFLICT DO NOTHING;

INSERT INTO "GemPackages"
("Title", "Amount", "BonusAmount", "Price", "OldPrice", "PlanType", "PurchaseType",
 "AutoDeliver", "G2BulkCatalogueName", "Stock", "IsAvailable", "IsActive", "SortOrder")
SELECT title, amount, 0, price, NULL, plan_type, 'by_credentials', false,
       catalogue, 9999, true, true, sort_order
FROM (VALUES
    ('📅 عضویت هفتگی فری‌فایر', 60, 100000, 'weekly', 'itunes_try:60', 10),
    ('📆 عضویت ماهانه فری‌فایر', 300, 500000, 'monthly', 'itunes_try:300', 20)
) AS seed(title, amount, price, plan_type, catalogue, sort_order)
WHERE NOT EXISTS (
    SELECT 1 FROM "GemPackages" p
    WHERE p."PurchaseType"='by_credentials' AND p."PlanType"=seed.plan_type
);

CREATE TABLE IF NOT EXISTS "GiftCardOrders" (
    "Id" SERIAL PRIMARY KEY,
    "OrderId" INTEGER NOT NULL UNIQUE REFERENCES "Orders"("Id") ON DELETE CASCADE,
    "OrderItemId" INTEGER REFERENCES "OrderItems"("Id") ON DELETE SET NULL,
    "Brand" VARCHAR(32) NOT NULL,
    "G2ProductId" INTEGER NOT NULL,
    "ProductTitle" VARCHAR(255) NOT NULL,
    "FaceLabel" VARCHAR(80) NOT NULL DEFAULT '',
    "CostUsd" NUMERIC(18,6) NOT NULL,
    "SaleToman" INTEGER NOT NULL CHECK ("SaleToman" > 0),
    "UsdTomanRate" INTEGER,
    "G2OrderId" VARCHAR(50),
    "G2Status" VARCHAR(30) NOT NULL DEFAULT '',
    "DeliveryCodes" TEXT NOT NULL DEFAULT '',
    "CreatedAt" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "UpdatedAt" TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO "BotSettings" ("Key","Value","UpdatedAt")
VALUES ('giftcard_profit_percent','15',now())
ON CONFLICT ("Key") DO NOTHING;

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
    "Amount" INTEGER NOT NULL,
    "BonusAmount" INTEGER NOT NULL DEFAULT 0,
    "Price" INTEGER NOT NULL,
    "OldPrice" INTEGER NULL,
    "PlanType" VARCHAR(20) NOT NULL DEFAULT 'once',
    "PurchaseType" VARCHAR(30) NOT NULL DEFAULT 'by_id',
    "AutoDeliver" BOOLEAN NOT NULL DEFAULT true,
    "G2BulkCatalogueName" VARCHAR(100),
    "Stock" INTEGER NOT NULL DEFAULT 9999,
    "IsAvailable" BOOLEAN NOT NULL DEFAULT true,
    "IsActive" BOOLEAN NOT NULL DEFAULT true,
    "CreatedAt" TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS "Orders" (
    "Id" SERIAL PRIMARY KEY,
    "UserId" INTEGER REFERENCES "Users"("Id"),
    "FullName" VARCHAR(255) NOT NULL DEFAULT '',
    "Email" VARCHAR(254) NOT NULL DEFAULT '',
    "Phone" VARCHAR(30) NOT NULL DEFAULT '',
    "TelegramId" VARCHAR(64),
    "TotalAmount" INTEGER NOT NULL,
    "DiscountAmount" INTEGER NOT NULL DEFAULT 0,
    "PaymentMethod" VARCHAR(30) NOT NULL DEFAULT 'pending',
    "PaymentAuthority" VARCHAR(100),
    "Status" VARCHAR(30) NOT NULL DEFAULT 'pending',
    "CreatedAt" TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS "OrderItems" (
    "Id" SERIAL PRIMARY KEY,
    "OrderId" INTEGER NOT NULL REFERENCES "Orders"("Id") ON DELETE CASCADE,
    "ProductId" INTEGER NULL,
    "ProductName" VARCHAR(255) NOT NULL,
    "Price" INTEGER NOT NULL,
    "Quantity" INTEGER NOT NULL DEFAULT 1
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
    "G2BulkOrderId" VARCHAR(50),
    "G2BulkStatus" VARCHAR(30)
);

CREATE TABLE IF NOT EXISTS "Wallets" (
    "Id" SERIAL PRIMARY KEY,
    "UserId" INTEGER UNIQUE NOT NULL REFERENCES "Users"("Id") ON DELETE CASCADE,
    "Balance" INTEGER NOT NULL DEFAULT 0,
    "UpdatedAt" TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS "WalletTransactions" (
    "Id" SERIAL PRIMARY KEY,
    "WalletId" INTEGER NOT NULL REFERENCES "Wallets"("Id") ON DELETE CASCADE,
    "Amount" INTEGER NOT NULL,
    "Kind" VARCHAR(10) NOT NULL DEFAULT 'charge',
    "Description" VARCHAR(255) NOT NULL DEFAULT '',
    "Authority" VARCHAR(100),
    "IsPaid" BOOLEAN NOT NULL DEFAULT false,
    "CreatedAt" TIMESTAMPTZ NOT NULL DEFAULT now()
);

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

-- بسته‌های جم ME (مثل سایت)
INSERT INTO "GemPackages"
("Title", "Amount", "BonusAmount", "Price", "OldPrice", "PlanType", "PurchaseType",
 "AutoDeliver", "G2BulkCatalogueName", "Stock", "IsAvailable", "IsActive")
VALUES
('بسته 110 جمی', 110, 0, 200000, NULL, 'once', 'by_id', true, '110', 9999, true, true),
('بسته 231 جمی', 231, 0, 400000, NULL, 'once', 'by_id', true, '231', 9999, true, true),
('بسته 583 جمی', 583, 0, 1000000, NULL, 'once', 'by_id', true, '583', 9999, true, true),
('بسته 1188 جمی', 1188, 0, 2000000, NULL, 'once', 'by_id', true, '1188', 9999, true, true),
('بسته 2420 جمی', 2420, 0, 4000000, NULL, 'once', 'by_id', true, '2420', 9999, true, true)
ON CONFLICT DO NOTHING;

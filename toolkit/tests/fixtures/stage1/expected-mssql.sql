CREATE TABLE [Customers] (
	[CustomerID] INTEGER NOT NULL IDENTITY(1,1), 
	[Company Name] NVARCHAR(50) NOT NULL, 
	[Active] BIT NOT NULL DEFAULT 1, 
	[Notes] NVARCHAR(max) NULL, 
	CONSTRAINT [Customers_pkey] PRIMARY KEY ([CustomerID])
)

CREATE UNIQUE INDEX [Customers_CompanyName] ON [Customers] ([Company Name])

CREATE TABLE [Order Lines] (
	[LineID] INTEGER NOT NULL IDENTITY(1,1), 
	[OrderID] INTEGER NOT NULL, 
	[Product] NVARCHAR(40) NULL DEFAULT 'TBD', 
	[Quantity] SMALLINT NOT NULL DEFAULT 1, 
	[Price] DECIMAL(19, 4) NULL, 
	[Discount] REAL NULL DEFAULT 0, 
	mp_rowversion ROWVERSION NOT NULL, 
	CONSTRAINT [Order Lines_pkey] PRIMARY KEY ([LineID])
)

CREATE UNIQUE INDEX [Order Lines_OrderProduct] ON [Order Lines] ([OrderID], [Product]) WHERE [Product] IS NOT NULL

CREATE TABLE [Orders] (
	[OrderID] INTEGER NOT NULL IDENTITY(1,1), 
	[CustomerID] INTEGER NOT NULL, 
	[OrderDate] DATETIME2(0) NOT NULL DEFAULT CURRENT_TIMESTAMP, 
	[Total] DECIMAL(19, 4) NULL DEFAULT 0, 
	[Shipped] BIT NOT NULL DEFAULT 0, 
	[Reference] NVARCHAR(20) NULL, 
	CONSTRAINT [Orders_pkey] PRIMARY KEY ([OrderID])
)

ALTER TABLE [Orders] ADD CONSTRAINT [CustomersOrders] FOREIGN KEY([CustomerID]) REFERENCES [Customers] ([CustomerID])

ALTER TABLE [Order Lines] ADD CONSTRAINT [OrdersOrderLines] FOREIGN KEY([OrderID]) REFERENCES [Orders] ([OrderID]) ON DELETE CASCADE

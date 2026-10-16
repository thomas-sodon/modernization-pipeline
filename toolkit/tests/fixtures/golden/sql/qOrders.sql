SELECT Orders.OrderID, Orders.Total AS Amount, Orders.Shipped
FROM Orders
ORDER BY Orders.OrderID DESC;

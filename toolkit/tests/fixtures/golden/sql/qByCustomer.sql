SELECT Orders.OrderID, Orders.Total
FROM Orders
WHERE (((Orders.CustID)=[Forms]![frmCust]![CustID]));

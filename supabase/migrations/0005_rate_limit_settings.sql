begin;
insert into system_settings(key,value,value_type,description,category,is_public) values
('guest_creation_rate_limit','6','integer','Guest sessions per IP cluster per hour','security',false),
('upload_rate_limit','8','integer','Upload pairs per owner per minute','security',false),
('websocket_connection_rate_limit','12','integer','Generation WebSocket connections per owner per minute','security',false),
('payment_rate_limit','5','integer','Checkout attempts per user per minute','security',false),
('admin_api_rate_limit','120','integer','Admin requests per user per minute','security',false)
on conflict(key) do nothing;
commit;

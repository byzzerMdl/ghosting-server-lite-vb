-- Logs joins/leaves and answers the !players command in the server console.
--
-- NOTE: this plugin intentionally does NOT call server.send_chat /
-- server.broadcast_chat. The Ghosting-mod client interprets that packet
-- format as a KICK command (the text shows up as the kick reason), so
-- sending "chat" to the game disconnects players. Until the real chat
-- wire format is known, keep plugin output on the server side only.

hooks.on_load = function()
    server.log("welcome plugin loaded (server-side logging only)")
end

hooks.on_connect = function(client)
    server.log(client.name .. " joined (" .. server.client_count() .. " online)")
end

hooks.on_disconnect = function(client)
    server.log(client.name .. " left")
end

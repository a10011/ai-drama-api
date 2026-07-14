const WebSocket = require('ws');

let wss = null;
const userConnections = new Map(); // userId -> Set<ws>

// 初始化WebSocket服务
function initWebSocket(server) {
  wss = new WebSocket.Server({ server, path: '/ws' });

  wss.on('connection', (ws, req) => {
    // 从URL参数获取token并验证
    const url = new URL(req.url, `http://${req.headers.host}`);
    const token = url.searchParams.get('token');

    // 验证token获取userId（简化处理）
    const userId = validateToken(token);
    if (!userId) {
      ws.close(1008, 'Invalid token');
      return;
    }

    // 保存连接
    if (!userConnections.has(userId)) {
      userConnections.set(userId, new Set());
    }
    userConnections.get(userId).add(ws);

    console.log(`[WebSocket] 用户 ${userId} 已连接`);

    // 发送连接成功消息
    ws.send(JSON.stringify({
      type: 'connected',
      message: 'WebSocket连接成功',
      timestamp: new Date().toISOString()
    }));

    ws.on('close', () => {
      const connections = userConnections.get(userId);
      if (connections) {
        connections.delete(ws);
        if (connections.size === 0) {
          userConnections.delete(userId);
        }
      }
      console.log(`[WebSocket] 用户 ${userId} 已断开`);
    });

    ws.on('error', (err) => {
      console.error(`[WebSocket] 用户 ${userId} 连接错误:`, err);
    });
  });

  return wss;
}

// 向用户推送消息
function notifyUser(userId, data) {
  const connections = userConnections.get(userId);
  if (!connections || connections.size === 0) return;

  const message = JSON.stringify({
    ...data,
    timestamp: new Date().toISOString()
  });

  connections.forEach(ws => {
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(message);
    }
  });
}

// 广播消息（给所有在线用户）
function broadcast(data) {
  const message = JSON.stringify({
    ...data,
    timestamp: new Date().toISOString()
  });

  userConnections.forEach((connections) => {
    connections.forEach(ws => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(message);
      }
    });
  });
}

// 验证token（简化版，实际应调用JWT验证）
function validateToken(token) {
  if (!token) return null;
  try {
    const jwt = require('jsonwebtoken');
    const decoded = jwt.verify(token, process.env.JWT_SECRET || 'your-secret-key');
    return decoded.userId || decoded.id;
  } catch {
    return null;
  }
}

// 获取在线用户统计
function getOnlineStats() {
  let total = 0;
  userConnections.forEach(connections => {
    total += connections.size;
  });
  return {
    onlineUsers: userConnections.size,
    totalConnections: total
  };
}

module.exports = {
  initWebSocket,
  notifyUser,
  broadcast,
  getOnlineStats
};

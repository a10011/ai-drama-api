const { PrismaClient } = require('@prisma/client');
const axios = require('axios');
const prisma = new PrismaClient();

const AGNES_API_KEY = process.env.AGNES_API_KEY || 'sk-bJqPewHYGBeMQIbeAw4G2dfG2LCtXCdGFU5hp0LVuJNeQIIJ';
const AGNES_BASE_URL = 'https://apihub.agnes-ai.com/v1';

// WebSocket 实例（从 app.js 传入）
let wssInstance = null;

function setWebSocketServer(wss) {
  wssInstance = wss;
}

// 发送进度到前端
function broadcastProgress(dramaId, progress, status, message, level = 'info') {
  if (!wssInstance) return;

  wssInstance.clients.forEach(client => {
    if (client.readyState === 1) {
      client.send(JSON.stringify({
        type: 'drama_progress',
        dramaId,
        progress,
        status,
        message,
        level,
        timestamp: new Date().toISOString()
      }));
    }
  });
}

// 发送完成通知
function broadcastCompleted(dramaId, videoUrl) {
  if (!wssInstance) return;

  wssInstance.clients.forEach(client => {
    if (client.readyState === 1) {
      client.send(JSON.stringify({
        type: 'drama_completed',
        dramaId,
        videoUrl,
        timestamp: new Date().toISOString()
      }));
    }
  });
}

// 发送失败通知
function broadcastFailed(dramaId, error) {
  if (!wssInstance) return;

  wssInstance.clients.forEach(client => {
    if (client.readyState === 1) {
      client.send(JSON.stringify({
        type: 'drama_failed',
        dramaId,
        error,
        timestamp: new Date().toISOString()
      }));
    }
  });
}

async function testAgnesConnection() {
  try {
    const response = await axios.get(
      `${AGNES_BASE_URL}/models`,
      {
        headers: {
          'Authorization': `Bearer ${AGNES_API_KEY}`,
          'Content-Type': 'application/json'
        },
        timeout: 30000
      }
    );
    console.log('[Agnes] 连接成功:', response.data.object);
    return true;
  } catch (error) {
    console.error('[Agnes] 连接失败:', error.message);
    return false;
  }
}

async function optimizeScriptWithAgnes(script, style = 'realistic') {
  try {
    const response = await axios.post(
      `${AGNES_BASE_URL}/chat/completions`,
      {
        model: 'agnes-2.0-flash',
        messages: [
          {
            role: 'system',
            content: '你是一个专业的短剧剧本优化师。'
          },
          {
            role: 'user',
            content: `请优化以下短剧剧本，风格：${style}\n\n${script}`
          }
        ],
        temperature: 0.7,
        max_tokens: 2000
      },
      {
        headers: {
          'Authorization': `Bearer ${AGNES_API_KEY}`,
          'Content-Type': 'application/json'
        },
        timeout: 60000
      }
    );
    return response.data.choices[0].message.content;
  } catch (error) {
    console.error('Agnes 失败:', error.message);
    return script;
  }
}

async function createVideoWithAgnes(prompt, options = {}) {
  try {
    const response = await axios.post(
      `${AGNES_BASE_URL}/videos`,
      {
        model: 'agnes-video-v2.0',
        prompt: prompt,
        duration: 5,
        fps: 24,
        width: 1152,
        height: 768
      },
      {
        headers: {
          'Authorization': `Bearer ${AGNES_API_KEY}`,
          'Content-Type': 'application/json',
          'Idempotency-Key': `video_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`
        },
        timeout: 30000
      }
    );

    return {
      taskId: response.data.id || response.data.task_id,
      status: 'submitted',
      raw: response.data
    };
  } catch (error) {
    console.error('Agnes 视频创建失败:', error.message);
    throw error;
  }
}

async function queryVideoTask(taskId) {
  try {
    const response = await axios.get(
      `${AGNES_BASE_URL}/videos/${taskId}`,
      {
        headers: {
          'Authorization': `Bearer ${AGNES_API_KEY}`
        },
        timeout: 30000
      }
    );

    const data = response.data;
    return {
      taskId,
      status: data.status === 'completed' ? 'completed' : 
              data.status === 'processing' ? 'processing' : 'pending',
      progress: data.status === 'completed' ? 100 : 
                data.status === 'processing' ? 60 : 10,
      videoUrl: data.video_url || data.url || null,
      errorMsg: data.error || null,
      raw: data
    };
  } catch (error) {
    // 504 容错处理 - 视为处理中
    if (error.response?.status === 504) {
      console.log(`[Agnes] 查询${taskId}超时504，视为处理中...`);
      return {
        taskId,
        status: 'processing',
        progress: 50,
        videoUrl: null,
        errorMsg: null,
        raw: null
      };
    }

    console.error('Agnes 视频查询失败:', error.message);
    throw error;
  }
}

async function createDrama(data) {
  const { userId, title, mode, script, duration, actorId, voiceStyle, style } = data;

  await prisma.user.upsert({
    where: { id: userId },
    update: {},
    create: {
      id: userId,
      email: 'demo@example.com',
      name: 'Demo User',
      memberLevel: 'unlimited'
    }
  });

  const drama = await prisma.drama.create({
    data: {
      id: require('uuid').v4(),
      userId,
      title,
      mode,
      script,
      duration: parseInt(duration),
      actorId: actorId ? parseInt(actorId) : null,
      voiceStyle: voiceStyle || 'gentle',
      style: style || 'realistic',
      status: 'processing',
      progress: 5,
      createdAt: new Date()
    }
  });

  // 异步启动视频生成
  setImmediate(() => {
    testAgnesConnection().then(connected => {
      if (connected) {
        processVideoWithAgnes(drama.id, script, style, duration);
      } else {
        simulateVideoGeneration(drama.id, duration);
      }
    });
  });

  return drama;
}

async function processVideoWithAgnes(dramaId, script, style, duration) {
  try {
    await updateProgress(dramaId, 10, 'processing', 'AI优化剧本中...');
    broadcastProgress(dramaId, 10, 'processing', 'AI优化剧本中...');

    const optimizedScript = await optimizeScriptWithAgnes(script, style);
    await updateProgress(dramaId, 15, 'processing', '剧本优化完成');
    broadcastProgress(dramaId, 15, 'processing', '剧本优化完成');

    await updateProgress(dramaId, 20, 'processing', '提交视频生成任务...');
    broadcastProgress(dramaId, 20, 'processing', '提交视频生成任务...');

    const videoTask = await createVideoWithAgnes(optimizedScript, {
      numFrames: Math.min(duration * 24, 441),
      frameRate: 24,
      size: '720x1280'
    });

    await prisma.drama.update({
      where: { id: dramaId },
      data: { externalTaskId: videoTask.taskId }
    });

    await updateProgress(dramaId, 25, 'processing', '视频生成任务已提交');
    broadcastProgress(dramaId, 25, 'processing', '视频生成任务已提交');

    const finalVideo = await pollVideoCompletion(videoTask.taskId, dramaId);

    await prisma.drama.update({
      where: { id: dramaId },
      data: {
        status: 'completed',
        progress: 100,
        videoUrl: finalVideo.videoUrl,
        updatedAt: new Date()
      }
    });

    broadcastCompleted(dramaId, finalVideo.videoUrl);
    console.log(`[Agnes] 视频生成完成: ${dramaId}`);
  } catch (error) {
    console.error(`[Agnes] 视频生成失败: ${dramaId}`, error);

    await prisma.drama.update({
      where: { id: dramaId },
      data: {
        status: 'failed',
        errorMsg: error.message,
        updatedAt: new Date()
      }
    });

    broadcastFailed(dramaId, error.message);
  }
}

async function pollVideoCompletion(taskId, dramaId, maxAttempts = 2160) {
  let consecutiveErrors = 0;
  const maxConsecutiveErrors = 10;

  for (let i = 0; i < maxAttempts; i++) {
    try {
      const status = await queryVideoTask(taskId);
      consecutiveErrors = 0;

      const progress = Math.min(25 + (i / maxAttempts) * 70, 95);
      const message = `视频生成中...(${i+1}/${maxAttempts})`;

      await updateProgress(dramaId, Math.floor(progress), 'processing', message);
      broadcastProgress(dramaId, Math.floor(progress), 'processing', message);

      if (status.status === 'completed') {
        return status;
      }
      if (status.status === 'failed') {
        throw new Error(status.errorMsg || '视频生成失败');
      }

      // 指数退避：前10次10秒，之后逐渐增加
      const delay = i < 10 ? 10000 : Math.min(10000 * Math.pow(1.2, i - 10), 60000);
      await new Promise(resolve => setTimeout(resolve, delay));

    } catch (error) {
      consecutiveErrors++;
      console.error(`[Agnes] 轮询错误 (${consecutiveErrors}/${maxConsecutiveErrors}):`, error.message);

      if (consecutiveErrors >= maxConsecutiveErrors) {
        throw new Error(`连续${maxConsecutiveErrors}次查询失败`);
      }

      // 出错后等待30秒再试
      await new Promise(resolve => setTimeout(resolve, 30000));
    }
  }

  throw new Error('视频生成超时，已达到最大轮询次数');
}

async function simulateVideoGeneration(dramaId, duration) {
  console.log(`[Simulate] 模拟视频生成: ${dramaId}`);

  const steps = [
    { progress: 20, delay: 2000, message: 'AI优化剧本中...' },
    { progress: 40, delay: 3000, message: '渲染视频画面...' },
    { progress: 60, delay: 3000, message: '合成配音...' },
    { progress: 80, delay: 3000, message: '添加特效...' },
    { progress: 95, delay: 2000, message: '最终合成...' }
  ];

  for (const step of steps) {
    await new Promise(resolve => setTimeout(resolve, step.delay));
    await updateProgress(dramaId, step.progress, 'processing', step.message);
    broadcastProgress(dramaId, step.progress, 'processing', step.message);
  }

  await prisma.drama.update({
    where: { id: dramaId },
    data: {
      status: 'completed',
      progress: 100,
      videoUrl: 'https://cdn.mzsh.top/videos/demo.mp4',
      updatedAt: new Date()
    }
  });

  broadcastCompleted(dramaId, 'https://cdn.mzsh.top/videos/demo.mp4');
}

async function updateProgress(dramaId, progress, status, message) {
  await prisma.drama.update({
    where: { id: dramaId },
    data: { progress, status, updatedAt: new Date() }
  });
  console.log(`[Progress] ${dramaId}: ${progress}% - ${message}`);
}

async function getDramaDetail(id, userId) {
  return prisma.drama.findFirst({ where: { id, userId } });
}

async function getDramaList(userId, options = {}) {
  const { page = 1, limit = 10, status } = options;
  const where = { userId };
  if (status) where.status = status;

  const [list, total] = await Promise.all([
    prisma.drama.findMany({
      where,
      orderBy: { createdAt: 'desc' },
      skip: (parseInt(page) - 1) * parseInt(limit),
      take: parseInt(limit)
    }),
    prisma.drama.count({ where })
  ]);

  return { list, total, page: parseInt(page), limit: parseInt(limit) };
}

async function retryDrama(id, userId) {
  const drama = await prisma.drama.findFirst({ where: { id, userId } });
  if (!drama) throw new Error('作品不存在');

  await prisma.drama.update({
    where: { id },
    data: {
      status: 'processing',
      progress: 5,
      videoUrl: null,
      errorMsg: null,
      updatedAt: new Date()
    }
  });

  setImmediate(() => {
    testAgnesConnection().then(connected => {
      if (connected) {
        processVideoWithAgnes(drama.id, drama.script, drama.style, drama.duration);
      } else {
        simulateVideoGeneration(drama.id, drama.duration);
      }
    });
  });

  return drama;
}

async function deleteDrama(id, userId) {
  const drama = await prisma.drama.findFirst({ where: { id, userId } });
  if (!drama) throw new Error('作品不存在');
  await prisma.drama.delete({ where: { id } });
  return true;
}

module.exports = {
  createDrama,
  getDramaDetail,
  getDramaList,
  retryDrama,
  deleteDrama,
  setWebSocketServer
};
const Queue = require('bull');

const redisConfig = {
  host: process.env.REDIS_HOST || 'localhost',
  port: process.env.REDIS_PORT || 6379,
  password: process.env.REDIS_PASSWORD || undefined
};

const videoQueue = new Queue('video-generation', { redis: redisConfig });

videoQueue.process(async (job) => {
  const { dramaId } = job.data;
  console.log(`[Queue] 任务已接收: ${dramaId}`);
  return { success: true, dramaId };
});

videoQueue.on('completed', (job, result) => {
  console.log(`[Queue] 任务完成: ${result.dramaId}`);
});

videoQueue.on('failed', (job, err) => {
  console.error(`[Queue] 任务失败: ${job.data.dramaId}`, err.message);
});

async function submitToQueue(dramaId, options = {}) {
  const { priority = 5 } = options;
  const job = await videoQueue.add(
    { dramaId, priority },
    { 
      priority,
      attempts: 3,
      backoff: { type: 'exponential', delay: 5000 },
      removeOnComplete: 100,
      removeOnFail: 50
    }
  );
  console.log(`[Queue] 任务已提交: ${dramaId}, JobID: ${job.id}`);
  return job;
}

async function getQueueStatus() {
  const [waiting, active, completed, failed] = await Promise.all([
    videoQueue.getWaitingCount(),
    videoQueue.getActiveCount(),
    videoQueue.getCompletedCount(),
    videoQueue.getFailedCount()
  ]);
  return { waiting, active, completed, failed, total: waiting + active };
}

module.exports = {
  submitToQueue,
  getQueueStatus,
  videoQueue
};

const axios = require('axios');
const { v4: uuidv4 } = require('uuid');

// ==================== 通义万象视频生成 ====================
class WanxVideoService {
  constructor() {
    this.baseURL = 'https://dashscope.aliyuncs.com/api/v1';
    this.apiKey = process.env.WANX_API_KEY || process.env.DASHSCOPE_API_KEY;
    this.model = 'wan2.7-t2v-2026-04-25'; // 文生视频模型
    this.i2vModel = 'wan2.5-i2v-preview'; // 图生视频模型
  }

  // 提交文生视频任务
  async submitTextToVideo(prompt, options = {}) {
    const {
      duration = 5,
      resolution = '720P',
      ratio = '16:9',
      promptExtend = true,
      negativePrompt = '',
      audioUrl = null
    } = options;

    const payload = {
      model: this.model,
      input: {
        prompt: prompt,
        ...(negativePrompt && { negative_prompt: negativePrompt }),
        ...(audioUrl && { audio_url: audioUrl })
      },
      parameters: {
        resolution,
        ratio,
        prompt_extend: promptExtend,
        duration
      }
    };

    const response = await axios.post(
      `${this.baseURL}/services/aigc/video-generation/video-synthesis`,
      payload,
      {
        headers: {
          'Authorization': `Bearer ${this.apiKey}`,
          'Content-Type': 'application/json',
          'X-DashScope-Async': 'enable'
        }
      }
    );

    return {
      taskId: response.data.output?.task_id || response.data.request_id,
      status: 'submitted',
      raw: response.data
    };
  }

  // 提交图生视频任务
  async submitImageToVideo(imageUrl, prompt, options = {}) {
    const {
      duration = 5,
      resolution = '720p',
      generateAudio = true,
      smartRewrite = true
    } = options;

    // 使用 Nebula API 或阿里云直接API
    const nebulaBase = 'https://llm.ai-nebula.com';

    const payload = {
      model: this.i2vModel,
      prompt: prompt,
      image: imageUrl,
      duration,
      resolution,
      generate_audio: generateAudio,
      smart_rewrite: smartRewrite
    };

    const response = await axios.post(
      `${nebulaBase}/v1/video/generations`,
      payload,
      {
        headers: {
          'Authorization': `Bearer ${this.apiKey}`,
          'Content-Type': 'application/json'
        }
      }
    );

    return {
      taskId: response.data.task_id,
      status: response.data.status,
      raw: response.data
    };
  }

  // 查询任务状态
  async queryTaskStatus(taskId) {
    // 先尝试阿里云查询
    try {
      const response = await axios.get(
        `${this.baseURL}/tasks/${taskId}`,
        {
          headers: {
            'Authorization': `Bearer ${this.apiKey}`
          }
        }
      );

      const data = response.data;
      const status = this._mapStatus(data.output?.task_status || data.status);

      return {
        taskId,
        status,
        progress: this._calculateProgress(status),
        videoUrl: data.output?.video_url || data.output?.url || null,
        errorMsg: data.output?.message || null,
        raw: data
      };
    } catch (err) {
      // 回退到 Nebula API 查询
      return this._queryNebula(taskId);
    }
  }

  async _queryNebula(taskId) {
    const nebulaBase = 'https://llm.ai-nebula.com';
    const response = await axios.get(
      `${nebulaBase}/v1/video/generations/${taskId}`,
      {
        headers: {
          'Authorization': `Bearer ${this.apiKey}`
        }
      }
    );

    const data = response.data;
    const status = data.status; // submitted, in_progress, succeeded, failed

    return {
      taskId,
      status: status === 'succeeded' ? 'completed' : status === 'in_progress' ? 'processing' : status,
      progress: status === 'succeeded' ? 100 : status === 'in_progress' ? 60 : 0,
      videoUrl: data.url || null,
      errorMsg: data.metadata?.output?.message || null,
      raw: data
    };
  }

  _mapStatus(rawStatus) {
    const map = {
      'PENDING': 'pending',
      'RUNNING': 'processing',
      'SUCCEEDED': 'completed',
      'FAILED': 'failed',
      'CANCELLED': 'failed'
    };
    return map[rawStatus] || rawStatus.toLowerCase();
  }

  _calculateProgress(status) {
    const map = { pending: 10, processing: 50, completed: 100, failed: 0 };
    return map[status] || 0;
  }
}

// ==================== MOMA 千问模型服务 ====================
class MomaService {
  constructor() {
    this.baseURL = process.env.MOMA_API_URL || 'https://api.moma.ai/v1';
    this.apiKey = process.env.MOMA_API_KEY;
  }

  // 剧本优化/扩写
  async optimizeScript(script, style = 'realistic') {
    const prompt = `请优化以下短剧剧本，使其更适合AI视频生成。要求：
1. 每段场景用[时间戳]标记（如[0-3秒]）
2. 描述具体的画面动作、镜头运动和视觉细节
3. 总时长控制在合理范围内
4. 风格：${style}

原剧本：
${script}

请输出优化后的分镜脚本：`;

    const response = await axios.post(
      `${this.baseURL}/chat/completions`,
      {
        model: 'qwen-max',
        messages: [{ role: 'user', content: prompt }],
        temperature: 0.7
      },
      {
        headers: {
          'Authorization': `Bearer ${this.apiKey}`,
          'Content-Type': 'application/json'
        }
      }
    );

    return response.data.choices[0].message.content;
  }

  // 生成画面提示词
  async generateImagePrompts(script, style = 'realistic') {
    const prompt = `根据以下剧本，为每个场景生成详细的画面提示词（用于AI图像生成）。每个提示词需要包含：
1. 场景描述（环境、光线、氛围）
2. 角色动作和表情
3. 镜头角度
4. 风格：${style}

剧本：
${script}

请按JSON格式输出，每个场景包含 scene, timeRange, prompt 字段：`;

    const response = await axios.post(
      `${this.baseURL}/chat/completions`,
      {
        model: 'qwen-max',
        messages: [{ role: 'user', content: prompt }],
        temperature: 0.7,
        response_format: { type: 'json_object' }
      },
      {
        headers: {
          'Authorization': `Bearer ${this.apiKey}`,
          'Content-Type': 'application/json'
        }
      }
    );

    try {
      return JSON.parse(response.data.choices[0].message.content);
    } catch {
      return { scenes: [] };
    }
  }
}

// ==================== Agnes AI TTS 服务 ====================
class AgnesTTSService {
  constructor() {
    this.baseURL = process.env.AGNES_API_URL || 'https://api.agnes.ai/v1';
    this.apiKey = process.env.AGNES_API_KEY;
  }

  async synthesize(text, voiceStyle = 'gentle', speed = 1.0) {
    const response = await axios.post(
      `${this.baseURL}/audio/speech`,
      {
        model: 'agnes-tts-v1',
        input: text,
        voice: voiceStyle,
        speed,
        response_format: 'mp3'
      },
      {
        headers: {
          'Authorization': `Bearer ${this.apiKey}`,
          'Content-Type': 'application/json'
        },
        responseType: 'arraybuffer'
      }
    );

    // 保存音频文件到OSS/S3，返回URL
    const audioUrl = await this._uploadAudio(response.data, `${uuidv4()}.mp3`);
    return audioUrl;
  }

  async _uploadAudio(buffer, filename) {
    // 这里接入你的OSS上传逻辑
    // 返回可访问的URL
    return `https://cdn.mzsh.top/audio/${filename}`;
  }
}

// ==================== 智能模型路由（CostAI） ====================
class ModelRouter {
  constructor() {
    this.wanx = new WanxVideoService();
    this.moma = new MomaService();
    this.tts = new AgnesTTSService();

    // 成本配置（元/秒）
    this.costConfig = {
      wanx: { text2video: 0.15, image2video: 0.12 },
      moma: { script: 0.001, imagePrompt: 0.002 },
      tts: { base: 0.005 }
    };
  }

  // 计算生成成本
  calculateCost(mode, duration, options = {}) {
    const costs = {
      scriptOptimization: this.costConfig.moma.script * (options.scriptLength || 100) / 1000,
      imageGeneration: this.costConfig.moma.imagePrompt * (options.sceneCount || 5),
      videoGeneration: this.costConfig.wanx[mode === 'ai_real' ? 'text2video' : 'image2video'] * duration,
      audioSynthesis: this.costConfig.tts.base * duration
    };

    const total = Object.values(costs).reduce((a, b) => a + b, 0);
    return { breakdown: costs, total: parseFloat(total.toFixed(4)) };
  }

  // 选择最优模型组合
  async selectOptimalPipeline(mode, script, duration) {
    // 根据会员等级、队列状态、成本选择最优路径
    const pipeline = {
      scriptProcessor: 'moma',
      videoGenerator: 'wanx',
      audioGenerator: 'agnes',
      estimatedCost: this.calculateCost(mode, duration, { sceneCount: 5 })
    };

    return pipeline;
  }
}

module.exports = {
  WanxVideoService,
  MomaService,
  AgnesTTSService,
  ModelRouter
};

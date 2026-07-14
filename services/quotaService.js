async function checkVideoQuota(userId, requiredSeconds) {
  return {
    sufficient: true,
    remaining: 999999,
    total: 999999,
    used: 0,
    memberLevel: 'unlimited'
  };
}

async function consumeQuota(userId, type, amount) {
  return;
}

async function getQuotaInfo(userId) {
  return {
    memberLevel: 'unlimited',
    video: { total: 999999, used: 0, remaining: 999999 },
    image: { total: 999999, used: 0, remaining: 999999 },
    text: { total: 999999, used: 0, remaining: 999999 }
  };
}

module.exports = {
  checkVideoQuota,
  consumeQuota,
  getQuotaInfo
};

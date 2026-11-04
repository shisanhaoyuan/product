<script setup lang="ts">
import { ref } from 'vue';
import { request } from '../api/client';
import { errorMessage } from '../utils/display';
import { useAuth } from '../composables/useAuth';
const { refreshAuth } = useAuth();
const username = ref(''),
  password = ref(''),
  error = ref(''),
  busy = ref(false);
async function login() {
  busy.value = true;
  error.value = '';
  try {
    await refreshAuth();
    await request('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: username.value, password: password.value }),
    });
    password.value = '';
    window.location.replace('/#/products');
    window.location.reload();
  } catch (reason) {
    error.value = errorMessage(reason);
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <section class="auth-card">
    <p class="eyebrow">JIWU / TEAM WORKSPACE</p>
    <h1>登录商品库</h1>
    <p class="subtitle">使用管理员分配的团队账号登录。</p>
    <form class="account-form" @submit.prevent="login">
      <label
        >账号<input
          v-model="username"
          class="text-input"
          autocomplete="username"
          required
          maxlength="64"
          :disabled="busy"
      /></label>
      <label
        >密码<input
          v-model="password"
          class="text-input"
          type="password"
          autocomplete="current-password"
          required
          maxlength="256"
          :disabled="busy"
      /></label>
      <button class="btn primary" :disabled="busy">{{ busy ? '正在登录…' : '登录' }}</button>
      <p v-if="error" class="error-box" role="alert">{{ error }}</p>
    </form>
  </section>
</template>

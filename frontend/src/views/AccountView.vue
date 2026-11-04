<script setup lang="ts">
import { ref } from 'vue';
import { request } from '../api/client';
import { useAuth } from '../composables/useAuth';
import { errorMessage } from '../utils/display';
const { auth } = useAuth();
const oldPassword = ref(''),
  newPassword = ref(''),
  confirm = ref(''),
  error = ref(''),
  busy = ref(false);
async function change() {
  error.value = '';
  if (confirm.value !== newPassword.value) {
    error.value = '两次新密码不一致';
    return;
  }
  busy.value = true;
  try {
    await request('/api/auth/password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ old_password: oldPassword.value, new_password: newPassword.value }),
    });
    window.location.replace('/#/login');
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
    <h1>账号与密码</h1>
    <p class="subtitle">{{ auth.user?.username }} · 修改密码后所有设备需重新登录。</p>
    <p v-if="auth.user?.must_change_password" class="notice warning">
      首次登录，请先将临时密码改为自己的密码。
    </p>
    <form class="account-form" @submit.prevent="change">
      <label
        >当前密码<input
          v-model="oldPassword"
          class="text-input"
          type="password"
          autocomplete="current-password"
          required
          :disabled="busy"
      /></label>
      <label
        >新密码（至少 12 位）<input
          v-model="newPassword"
          class="text-input"
          type="password"
          autocomplete="new-password"
          required
          minlength="12"
          maxlength="256"
          :disabled="busy"
      /></label>
      <label
        >确认新密码<input
          v-model="confirm"
          class="text-input"
          type="password"
          autocomplete="new-password"
          required
          minlength="12"
          maxlength="256"
          :disabled="busy"
      /></label>
      <button class="btn primary" :disabled="busy">修改并重新登录</button>
      <p v-if="error" class="error-box" role="alert">{{ error }}</p>
    </form>
  </section>
</template>

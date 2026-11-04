<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { request } from '../api/client';
import { useAuth, type User } from '../composables/useAuth';
import { errorMessage } from '../utils/display';
const { auth } = useAuth();
const users = ref<User[]>([]),
  username = ref(''),
  password = ref(''),
  role = ref('viewer'),
  busy = ref(false),
  error = ref('');
const labels = { admin: '管理员', editor: '编辑员', viewer: '只读成员' };
const audit = ref<
  { id: number; at: number; username: string | null; action: string; target: string }[]
>([]);
async function load() {
  [users.value, audit.value] = await Promise.all([
    request<User[]>('/api/admin/users'),
    request<typeof audit.value>('/api/admin/audit'),
  ]);
}
onMounted(() =>
  load().catch((reason) => {
    error.value = errorMessage(reason);
  }),
);
async function create() {
  busy.value = true;
  error.value = '';
  try {
    await request('/api/admin/users', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        username: username.value,
        password: password.value,
        role: role.value,
      }),
    });
    username.value = '';
    password.value = '';
    await load();
  } catch (reason) {
    error.value = errorMessage(reason);
  } finally {
    busy.value = false;
  }
}
async function update(user: User, nextRole: string, active: boolean) {
  busy.value = true;
  error.value = '';
  try {
    await request('/api/admin/users/' + user.id, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ role: nextRole, active }),
    });
    await load();
  } catch (reason) {
    error.value = errorMessage(reason);
    await load().catch(() => {});
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <div class="page-heading">
    <div>
      <p class="eyebrow">TEAM ACCESS</p>
      <h1>团队账号</h1>
    </div>
  </div>
  <p class="subtitle">
    管理员管理账号与导入，编辑员可以导入，只读成员可查看和导出。所有成员访问整个商品库，暂不按店铺隔离。
  </p>
  <p v-if="error" class="error-box" role="alert">{{ error }}</p>
  <div class="integration-grid">
    <section class="section">
      <div class="section-head"><h2>成员</h2></div>
      <div class="table-scroll">
        <table class="data-table">
          <thead>
            <tr>
              <th>账号</th>
              <th>权限</th>
              <th>状态</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="user in users" :key="user.id">
              <td>{{ user.username }}</td>
              <td>
                <select
                  class="filter"
                  :aria-label="user.username + '的角色'"
                  :value="user.role"
                  :disabled="busy || user.id === auth.user?.id"
                  @change="update(user, ($event.target as HTMLSelectElement).value, !!user.active)"
                >
                  <option v-for="(label, value) in labels" :key="value" :value="value">
                    {{ label }}
                  </option>
                </select>
              </td>
              <td>{{ user.active ? '启用' : '禁用' }}</td>
              <td>
                <button
                  class="btn small"
                  :disabled="busy || user.id === auth.user?.id"
                  @click="update(user, user.role, !user.active)"
                >
                  {{ user.active ? '禁用' : '启用' }}
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
    <section class="section">
      <div class="section-head"><h2>添加成员</h2></div>
      <form class="account-form" @submit.prevent="create">
        <label
          >账号<input
            v-model="username"
            class="text-input"
            required
            pattern="[a-z0-9][a-z0-9_.-]{2,63}"
            autocomplete="off"
            :disabled="busy"
            placeholder="3–64 位小写字母、数字、点、横线或下划线"
        /></label>
        <label
          >临时密码<input
            v-model="password"
            class="text-input"
            type="password"
            minlength="12"
            maxlength="256"
            autocomplete="new-password"
            required
            :disabled="busy"
        /></label>
        <label
          >权限<select v-model="role" class="filter" :disabled="busy">
            <option v-for="(label, value) in labels" :key="value" :value="value">
              {{ label }}
            </option>
          </select></label
        >
        <p class="subtitle">新成员首次登录必须修改密码。禁用账号或变更权限会立即撤销其已有登录。</p>
        <button class="btn primary" :disabled="busy">添加成员</button>
      </form>
    </section>
  </div>
  <section class="section">
    <div class="section-head"><h2>最近操作记录</h2></div>
    <div class="table-scroll">
      <table class="data-table">
        <thead>
          <tr>
            <th>时间</th>
            <th>账号</th>
            <th>操作</th>
            <th>对象</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="event in audit" :key="event.id">
            <td>{{ new Date(event.at * 1000).toLocaleString() }}</td>
            <td>{{ event.username || '服务器管理' }}</td>
            <td>{{ event.action }}</td>
            <td>{{ event.target }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

import { createApp } from 'vue';
import App from './App.vue';
import { router } from './router';
import './styles/theme.css';
import './styles/giga.css';

createApp(App).use(router).mount('#root');

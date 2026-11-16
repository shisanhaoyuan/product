import { createApp } from 'vue';
import App from './App.vue';
import { router } from './router';
import './styles/theme.css';
import './styles/giga.css';
import './styles/publishing.css';

createApp(App).use(router).mount('#root');

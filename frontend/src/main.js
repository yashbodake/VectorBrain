import { createApp } from 'vue'
import { createPinia } from 'pinia'
import './style.css'
import App from './App.vue'

// Pinia is the only state layer; components read/write via stores, never via
// fetch directly (coding-conventions.md).
createApp(App).use(createPinia()).mount('#app')

import './styles.css';
import App from './App.svelte';
import { mount } from 'svelte';

const target = document.getElementById('app');
if (!target) throw new Error('CareLoop simulator: #app container is missing from index.html');

const app = mount(App, { target });

export default app;

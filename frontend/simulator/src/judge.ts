import './styles.css';
import Judge from './Judge.svelte';
import { mount } from 'svelte';

const target = document.getElementById('app');
if (!target) throw new Error('CareLoop judge console: #app container is missing from judge.html');

const app = mount(Judge, { target });

export default app;

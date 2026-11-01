// Native Electron bundles are reviewed separately from the Linux repackaging.
// 4.4212.0 ships its renderer in ion-dist.asar on both native platforms.
const SESSION = "c11959232-CD9eXeQE.js";
const TOOLBAR = "c73f45bc3-BEJjk8iW.js";
const PANE = "c9bd7af9c-ChJIedqp.js";
const SSH = 'import{q as agentViewIsSsh}from"./c5da7eb42-D8jtksf3.js";';
const VIEW = '({label:X().formatMessage({defaultMessage:"View",id:"LCWUQ/4Fu6"}),submenu:[';
const PROTOCOL = 'let t=new URL(e.url);if(t.hostname!=="localhost")return new Response(null,{status:404});let n=e.headers.get("Origin")';

// These two expressions were memoized by the React compiler. Build them on each
// render so an SSH-kind change cannot leave a stale label, icon or callback.
const BUTTON = '(e,t,n,r,i)=>{let a=i!==void 0&&i;return t||a?[{pane:e,icon:e==="preview"&&T?"Globe":ul[e],label:n,shortcut:r,active:f.has(e),toggle:()=>g(e,a),intent:e==="preview"?k:void 0,explainsUnavailable:a}]:[]}';
const PREVIEW = 'e("preview",!!D,T?r.formatMessage({defaultMessage:"Browser",id:"E3oB+tM0Ou"}):r.formatMessage({defaultMessage:"Preview",id:"GD2Vvn5YZe"}),Ve("togglePreview",w,{externalPreviewAvailable:T}),!E&&O.preview!==void 0)';

export function nativeBuild({ chunk, menu, toggle, panes, csp }) {
  return {
    chunk,
    main: [
      [menu + VIEW, menu + VIEW + `require("./agentViewLauncher.js").menuItem({electron:u,toggleSidebar:event=>${toggle}(event,dispatcher=>dispatcher.dispatchToggleSessionPane(${panes}.Preview),null),logger:N}),{type:"separator"},`],
      [`csp:${csp}`, `csp:${csp}.replace(/frame-src([^;]*)/,directive=>directive+" app://agent-view")`],
      [PROTOCOL, 'let t=new URL(e.url);if(t.hostname==="agent-view")return require("./agentViewLauncher.js").proxyRequest(e,u);' + PROTOCOL.slice("let t=new URL(e.url);".length)],
    ],
    frontend: {
      [SESSION]: ["6bf4b2da4bbfb8da457e1bf090a8daf3a99061964d86810ee253abbec2aa5f7b", [
        ["import{n as e,r as t,t as n}", SSH + "import{n as e,r as t,t as n}"],
        ["W=R||ne.preview!==void 0", "agentViewSsh=agentViewIsSsh(l),W=R||agentViewSsh||ne.preview!==void 0"],
      ]],
      [TOOLBAR]: ["d885639719edc608d77d14e25dcc1dcc26e693b9c7f5daa2e7511304f3475a5c", [
        ['import{n as e,t}from"./preload-helper-Bc6KxGtA.js";', SSH + 'import{n as e,t}from"./preload-helper-Bc6KxGtA.js";'],
        ["T=Cn(o?.type),E=sp(i,l)", "agentViewSsh=agentViewIsSsh(o),T=Cn(o?.type)||agentViewSsh,E=sp(i,l)||agentViewSsh"],
        [`J4($,50,f,T,k,g)?K4($,50,f,T,k,g,${BUTTON}):$[54]`, BUTTON],
        [`J5($,55,D,T,e,r,w,E,O)?K5($,55,D,T,e,r,w,E,O,${PREVIEW}):$[62]`, PREVIEW.replace('!!D,T?', '!!D,agentViewSsh?"Agent View":T?')],
      ]],
      [PANE]: ["9c1f8b65e9bff2a7c392506ecb0bb47b9c67925b309c8ab23c68af6dc593087c", [
        ['import{A as e,T as t,d as n,u as r,w as i,y as a}', SSH + 'import{A as e,T as t,d as n,u as r,w as i,y as a}'],
        ['xt=J3($,116,t,L,j)', 'agentViewSshHook=agentViewIsSsh(j),agentViewSsh=j?.type==="local"&&agentViewSshHook,xt=J3($,116,t,L,j)'],
        ['St=J1($,120,xt)?K1($,120,xt,xt&&!wr()):$[121]', 'St=(J1($,120,xt)?K1($,120,xt,xt&&!wr()):$[121])&&!agentViewSsh'],
        ['let Et=J5($,154,', 'let Et=t==="preview"&&agentViewSsh?z("iframe",{key:j.id,title:"Agent View",src:"app://agent-view/?desktop_session="+encodeURIComponent(j.id),sandbox:"allow-scripts allow-same-origin",referrerPolicy:"no-referrer",style:{width:"100%",height:"100%",minHeight:0,flex:1,border:0,background:"#fff"}}):J5($,154,'],
        ['Ot=J5($,187,', 'Ot=t==="preview"&&agentViewSsh?"Agent View":J5($,187,'],
      ]],
    },
    // The injected hook is itself versioned and pinned, along with its callers.
    dependencies: { "c5da7eb42-D8jtksf3.js": "c297177b68a22c1c514c3d86a921ccde1cd0d11abb03c66c351e20e1c9941ce9" },
  };
}

const WINDOWS = nativeBuild({ chunk: ".vite/build/index.chunk-2SHPxESV.js",
  menu: "Fqi=e=>", toggle: "y0", panes: "dv", csp: "LHe(Wl(t),c,l)" });
WINDOWS.mainHash = "716599ebde5c1e17f303cd0c736c4472b463e58ed127a278853cc2406d2eabb4";
export const NATIVE_BUILDS = { win32: { "4.4212.0": WINDOWS } };

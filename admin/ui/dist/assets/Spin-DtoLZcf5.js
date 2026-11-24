import{h as C,k as o,E as S,D as x,c as m,r as k}from"./vendor-vue-01Qz8dz3.js";import{q as f,n as c,aY as $,o as h,a7 as w,y as T,z as v,W as R,aZ as O,a_ as j,E as B}from"./index-BvVhdLnX.js";import{v as N,d as P}from"./vendor-naive-SrciwSnN.js";const E=f([f("@keyframes spin-rotate",`
 from {
 transform: rotate(0);
 }
 to {
 transform: rotate(360deg);
 }
 `),c("spin-container",`
 position: relative;
 `,[c("spin-body",`
 position: absolute;
 top: 50%;
 left: 50%;
 transform: translateX(-50%) translateY(-50%);
 `,[$()])]),c("spin-body",`
 display: inline-flex;
 align-items: center;
 justify-content: center;
 flex-direction: column;
 `),c("spin",`
 display: inline-flex;
 height: var(--n-size);
 width: var(--n-size);
 font-size: var(--n-size);
 color: var(--n-color);
 `,[h("rotate",`
 animation: spin-rotate 2s linear infinite;
 `)]),c("spin-description",`
 display: inline-block;
 font-size: var(--n-font-size);
 color: var(--n-text-color);
 transition: color .3s var(--n-bezier);
 margin-top: 8px;
 `),c("spin-content",`
 opacity: 1;
 transition: opacity .3s var(--n-bezier);
 pointer-events: all;
 `,[h("spinning",`
 user-select: none;
 -webkit-user-select: none;
 pointer-events: none;
 opacity: var(--n-opacity-spinning);
 `)])]),V={small:20,medium:18,large:16},W=Object.assign(Object.assign(Object.assign({},v.props),{contentClass:String,contentStyle:[Object,String],description:String,size:{type:[String,Number],default:"medium"},show:{type:Boolean,default:!0},rotate:{type:Boolean,default:!0},spinning:{type:Boolean,validator:()=>!0,default:void 0},delay:Number}),O),L=C({name:"Spin",props:W,slots:Object,setup(e){const{mergedClsPrefixRef:r,inlineThemeDisabled:t}=T(e),s=v("Spin","-spin",E,j,e,r),d=m(()=>{const{size:n}=e,{common:{cubicBezierEaseInOut:a},self:u}=s.value,{opacitySpinning:y,color:g,textColor:b}=u,z=typeof n=="number"?P(n):u[B("size",n)];return{"--n-bezier":a,"--n-opacity-spinning":y,"--n-size":z,"--n-color":g,"--n-text-color":b}}),i=t?R("spin",m(()=>{const{size:n}=e;return typeof n=="number"?String(n):n[0]}),d,e):void 0,p=N(e,["spinning","show"]),l=k(!1);return x(n=>{let a;if(p.value){const{delay:u}=e;if(u){a=window.setTimeout(()=>{l.value=!0},u),n(()=>{clearTimeout(a)});return}}l.value=p.value}),{mergedClsPrefix:r,active:l,mergedStrokeWidth:m(()=>{const{strokeWidth:n}=e;if(n!==void 0)return n;const{size:a}=e;return V[typeof a=="number"?"medium":a]}),cssVars:t?void 0:d,themeClass:i?.themeClass,onRender:i?.onRender}},render(){var e,r;const{$slots:t,mergedClsPrefix:s,description:d}=this,i=t.icon&&this.rotate,p=(d||t.description)&&o("div",{class:`${s}-spin-description`},d||((e=t.description)===null||e===void 0?void 0:e.call(t))),l=t.icon?o("div",{class:[`${s}-spin-body`,this.themeClass]},o("div",{class:[`${s}-spin`,i&&`${s}-spin--rotate`],style:t.default?"":this.cssVars},t.icon()),p):o("div",{class:[`${s}-spin-body`,this.themeClass]},o(w,{clsPrefix:s,style:t.default?"":this.cssVars,stroke:this.stroke,"stroke-width":this.mergedStrokeWidth,radius:this.radius,scale:this.scale,class:`${s}-spin`}),p);return(r=this.onRender)===null||r===void 0||r.call(this),t.default?o("div",{class:[`${s}-spin-container`,this.themeClass],style:this.cssVars},o("div",{class:[`${s}-spin-content`,this.active&&`${s}-spin-content--spinning`,this.contentClass],style:this.contentStyle},t),o(S,{name:"fade-in-transition"},{default:()=>this.active?l:null})):l}});export{L as N};

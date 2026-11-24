import{b4 as N,n as E,w as v,q as F,E as H,F as w,J as O}from"./index-BWees6yH.js";import{K as V,d as b}from"./vendor-naive-Ba-Gl6yc.js";import{h as j,k,m as A,F as K,c as y}from"./vendor-vue-DkcyLO0F.js";let S=!1;function L(){if(N&&window.CSS&&!S&&(S=!0,"registerProperty"in window?.CSS))try{CSS.registerProperty({name:"--n-color-start",syntax:"<color>",inherits:!1,initialValue:"#0000"}),CSS.registerProperty({name:"--n-color-end",syntax:"<color>",inherits:!1,initialValue:"#0000"})}catch{}}function T(e){const{heightSmall:i,heightMedium:n,heightLarge:a,borderRadius:s}=e;return{color:"#eee",colorEnd:"#ddd",borderRadius:s,heightSmall:i,heightMedium:n,heightLarge:a}}const $={common:E,self:T},q=v([F("skeleton",`
 height: 1em;
 width: 100%;
 transition:
 --n-color-start .3s var(--n-bezier),
 --n-color-end .3s var(--n-bezier),
 background-color .3s var(--n-bezier);
 animation: 2s skeleton-loading infinite cubic-bezier(0.36, 0, 0.64, 1);
 background-color: var(--n-color-start);
 `),v("@keyframes skeleton-loading",`
 0% {
 background: var(--n-color-start);
 }
 40% {
 background: var(--n-color-end);
 }
 80% {
 background: var(--n-color-start);
 }
 100% {
 background: var(--n-color-start);
 }
 `)]),I=Object.assign(Object.assign({},w.props),{text:Boolean,round:Boolean,circle:Boolean,height:[String,Number],width:[String,Number],size:String,repeat:{type:Number,default:1},animated:{type:Boolean,default:!0},sharp:{type:Boolean,default:!0}}),D=j({name:"Skeleton",inheritAttrs:!1,props:I,setup(e){L();const{mergedClsPrefixRef:i,mergedComponentPropsRef:n}=H(e),a=y(()=>{var r,o;return e.size||((o=(r=n?.value)===null||r===void 0?void 0:r.Skeleton)===null||o===void 0?void 0:o.size)}),s=w("Skeleton","-skeleton",q,$,e,i);return{mergedClsPrefix:i,style:y(()=>{var r,o;const g=s.value,{common:{cubicBezierEaseInOut:z}}=g,h=g.self,{color:x,colorEnd:C,borderRadius:P}=h;let l;const{circle:d,sharp:B,round:R,width:t,height:c,text:f,animated:_}=e,p=a.value;p!==void 0&&(l=h[O("height",p)]);const u=d?(r=t??c)!==null&&r!==void 0?r:l:t,m=(o=d?t??c:c)!==null&&o!==void 0?o:l;return{display:f?"inline-block":"",verticalAlign:f?"-0.125em":"",borderRadius:d?"50%":R?"4096px":B?"":P,width:typeof u=="number"?b(u):u,height:typeof m=="number"?b(m):m,animation:_?"":"none","--n-bezier":z,"--n-color-start":x,"--n-color-end":C}})}},render(){const{repeat:e,style:i,mergedClsPrefix:n,$attrs:a}=this,s=k("div",A({class:`${n}-skeleton`,style:i},a));return e>1?k(K,null,V(e,null).map(r=>[s,`
`])):s}});export{D as N};

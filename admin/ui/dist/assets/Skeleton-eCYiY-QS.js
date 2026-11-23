import{aw as N,t as O,D as p,y as A,ax as E,L as H,O as w,U as L,av as b}from"./index-B0zX3rEi.js";import{x as V,A as k,K as j,F,d as y}from"./useTheme-5py-AQOR.js";let S=!1;function K(){if(N&&window.CSS&&!S&&(S=!0,"registerProperty"in window?.CSS))try{CSS.registerProperty({name:"--n-color-start",syntax:"<color>",inherits:!1,initialValue:"#0000"}),CSS.registerProperty({name:"--n-color-end",syntax:"<color>",inherits:!1,initialValue:"#0000"})}catch{}}function T(e){const{heightSmall:i,heightMedium:n,heightLarge:s,borderRadius:a}=e;return{color:"#eee",colorEnd:"#ddd",borderRadius:a,heightSmall:i,heightMedium:n,heightLarge:s}}const $={common:O,self:T},D=p([A("skeleton",`
 height: 1em;
 width: 100%;
 transition:
 --n-color-start .3s var(--n-bezier),
 --n-color-end .3s var(--n-bezier),
 background-color .3s var(--n-bezier);
 animation: 2s skeleton-loading infinite cubic-bezier(0.36, 0, 0.64, 1);
 background-color: var(--n-color-start);
 `),p("@keyframes skeleton-loading",`
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
 `)]),I=Object.assign(Object.assign({},w.props),{text:Boolean,round:Boolean,circle:Boolean,height:[String,Number],width:[String,Number],size:String,repeat:{type:Number,default:1},animated:{type:Boolean,default:!0},sharp:{type:Boolean,default:!0}}),W=V({name:"Skeleton",inheritAttrs:!1,props:I,setup(e){K();const{mergedClsPrefixRef:i,mergedComponentPropsRef:n}=H(e),s=y(()=>{var r,t;return e.size||((t=(r=n?.value)===null||r===void 0?void 0:r.Skeleton)===null||t===void 0?void 0:t.size)}),a=w("Skeleton","-skeleton",D,$,e,i);return{mergedClsPrefix:i,style:y(()=>{var r,t;const g=a.value,{common:{cubicBezierEaseInOut:x}}=g,h=g.self,{color:z,colorEnd:C,borderRadius:P}=h;let l;const{circle:d,sharp:B,round:R,width:o,height:c,text:f,animated:_}=e,v=s.value;v!==void 0&&(l=h[L("height",v)]);const u=d?(r=o??c)!==null&&r!==void 0?r:l:o,m=(t=d?o??c:c)!==null&&t!==void 0?t:l;return{display:f?"inline-block":"",verticalAlign:f?"-0.125em":"",borderRadius:d?"50%":R?"4096px":B?"":P,width:typeof u=="number"?b(u):u,height:typeof m=="number"?b(m):m,animation:_?"":"none","--n-bezier":x,"--n-color-start":z,"--n-color-end":C}})}},render(){const{repeat:e,style:i,mergedClsPrefix:n,$attrs:s}=this,a=k("div",j({class:`${n}-skeleton`,style:i},s));return e>1?k(F,null,E(e,null).map(r=>[a,`
`])):a}});export{W as N};

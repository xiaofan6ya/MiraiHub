import{aq as N,j as A,q as p,n as j,ar as E,y as H,A as w,G as O,an as b}from"./index-h_AcSpnM.js";import{x as V,A as k,K as q,F,d as y}from"./_plugin-vue_export-helper-GEDmboVY.js";let S=!1;function K(){if(N&&window.CSS&&!S&&(S=!0,"registerProperty"in window?.CSS))try{CSS.registerProperty({name:"--n-color-start",syntax:"<color>",inherits:!1,initialValue:"#0000"}),CSS.registerProperty({name:"--n-color-end",syntax:"<color>",inherits:!1,initialValue:"#0000"})}catch{}}function L(e){const{heightSmall:i,heightMedium:n,heightLarge:s,borderRadius:a}=e;return{color:"#eee",colorEnd:"#ddd",borderRadius:a,heightSmall:i,heightMedium:n,heightLarge:s}}const T={common:A,self:L},$=p([j("skeleton",`
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
 `)]),G=Object.assign(Object.assign({},w.props),{text:Boolean,round:Boolean,circle:Boolean,height:[String,Number],width:[String,Number],size:String,repeat:{type:Number,default:1},animated:{type:Boolean,default:!0},sharp:{type:Boolean,default:!0}}),W=V({name:"Skeleton",inheritAttrs:!1,props:G,setup(e){K();const{mergedClsPrefixRef:i,mergedComponentPropsRef:n}=H(e),s=y(()=>{var r,o;return e.size||((o=(r=n?.value)===null||r===void 0?void 0:r.Skeleton)===null||o===void 0?void 0:o.size)}),a=w("Skeleton","-skeleton",$,T,e,i);return{mergedClsPrefix:i,style:y(()=>{var r,o;const g=a.value,{common:{cubicBezierEaseInOut:z}}=g,h=g.self,{color:x,colorEnd:C,borderRadius:P}=h;let l;const{circle:d,sharp:B,round:R,width:t,height:c,text:f,animated:_}=e,v=s.value;v!==void 0&&(l=h[O("height",v)]);const u=d?(r=t??c)!==null&&r!==void 0?r:l:t,m=(o=d?t??c:c)!==null&&o!==void 0?o:l;return{display:f?"inline-block":"",verticalAlign:f?"-0.125em":"",borderRadius:d?"50%":R?"4096px":B?"":P,width:typeof u=="number"?b(u):u,height:typeof m=="number"?b(m):m,animation:_?"":"none","--n-bezier":z,"--n-color-start":x,"--n-color-end":C}})}},render(){const{repeat:e,style:i,mergedClsPrefix:n,$attrs:s}=this,a=k("div",q({class:`${n}-skeleton`,style:i},s));return e>1?k(F,null,E(e,null).map(r=>[a,`
`])):a}});export{W as N};

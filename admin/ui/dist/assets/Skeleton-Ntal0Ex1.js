import{a9 as N,k as E,q as v,n as H,y as O,z as w,E as V}from"./index-CSUQ7re2.js";import{H as j,d as b}from"./vendor-naive-SrciwSnN.js";import{h as A,k,m as F,F as L,c as y}from"./vendor-vue-01Qz8dz3.js";let S=!1;function T(){if(N&&window.CSS&&!S&&(S=!0,"registerProperty"in window?.CSS))try{CSS.registerProperty({name:"--n-color-start",syntax:"<color>",inherits:!1,initialValue:"#0000"}),CSS.registerProperty({name:"--n-color-end",syntax:"<color>",inherits:!1,initialValue:"#0000"})}catch{}}function $(e){const{heightSmall:i,heightMedium:n,heightLarge:a,borderRadius:s}=e;return{color:"#eee",colorEnd:"#ddd",borderRadius:s,heightSmall:i,heightMedium:n,heightLarge:a}}const q={common:E,self:$},I=v([H("skeleton",`
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
 `)]),K=Object.assign(Object.assign({},w.props),{text:Boolean,round:Boolean,circle:Boolean,height:[String,Number],width:[String,Number],size:String,repeat:{type:Number,default:1},animated:{type:Boolean,default:!0},sharp:{type:Boolean,default:!0}}),G=A({name:"Skeleton",inheritAttrs:!1,props:K,setup(e){T();const{mergedClsPrefixRef:i,mergedComponentPropsRef:n}=O(e),a=y(()=>{var r,o;return e.size||((o=(r=n?.value)===null||r===void 0?void 0:r.Skeleton)===null||o===void 0?void 0:o.size)}),s=w("Skeleton","-skeleton",I,q,e,i);return{mergedClsPrefix:i,style:y(()=>{var r,o;const g=s.value,{common:{cubicBezierEaseInOut:z}}=g,h=g.self,{color:x,colorEnd:C,borderRadius:P}=h;let l;const{circle:d,sharp:B,round:R,width:t,height:c,text:f,animated:_}=e,p=a.value;p!==void 0&&(l=h[V("height",p)]);const u=d?(r=t??c)!==null&&r!==void 0?r:l:t,m=(o=d?t??c:c)!==null&&o!==void 0?o:l;return{display:f?"inline-block":"",verticalAlign:f?"-0.125em":"",borderRadius:d?"50%":R?"4096px":B?"":P,width:typeof u=="number"?b(u):u,height:typeof m=="number"?b(m):m,animation:_?"":"none","--n-bezier":z,"--n-color-start":x,"--n-color-end":C}})}},render(){const{repeat:e,style:i,mergedClsPrefix:n,$attrs:a}=this,s=k("div",F({class:`${n}-skeleton`,style:i},a));return e>1?k(L,null,j(e,null).map(r=>[s,`
`])):s}});export{G as N};
